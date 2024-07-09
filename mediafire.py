import hashlib
import re
import time
import gazpacho
import requests
import os
import threading


class MediafireDownloader:
    def __init__(self):
        self.NON_ALPHANUM_FILE_OR_FOLDER_NAME_CHARACTERS = "-_. "
        self.NON_ALPHANUM_FILE_OR_FOLDER_NAME_CHARACTER_REPLACEMENT = "-"

    def hash_file(self, filename: str) -> str:
        h = hashlib.sha256()
        with open(filename, "rb") as file:
            chunk = 0
            while chunk != b"":
                chunk = file.read(1024)
                h.update(chunk)
        return h.hexdigest()

    def normalize_file_or_folder_name(self, filename: str) -> str:
        return "".join(
            [
                char
                if (char.isalnum() or char in self.NON_ALPHANUM_FILE_OR_FOLDER_NAME_CHARACTERS)
                else self.NON_ALPHANUM_FILE_OR_FOLDER_NAME_CHARACTER_REPLACEMENT
                for char in filename
            ]
        )

    def print_error(self, link: str):
        print(
            f"Error: Deleted file or Dangerous File Blocked\n"
            f"Take a look if you want to be sure: {link}"
        )

    def main(self):
        links_file = input("Please enter the file path containing Mediafire links: ").strip('"')
        output_path = input("Please enter the output folder (default is current directory): ") or "."
        threads_num = input("Please enter the number of threads to use (default is 10): ")
        threads_num = int(threads_num) if threads_num else 10

        links = self.extract_links_from_file(links_file)

        for mediafire_url in links:
            folder_or_file = re.findall(
                r"mediafire\.com/(folder|file|file_premium)/([a-zA-Z0-9]+)", mediafire_url
            )

            if not folder_or_file:
                print(f"Invalid link: {mediafire_url}")
                continue

            t, key = folder_or_file[0]

            if t in {"file", "file_premium"}:
                file_data = requests.get(self.get_info_endpoint(key)).json()["response"]["file_info"]

                if output_path:
                    os.chdir(output_path)

                self.download_file(file_data)
            elif t == "folder":
                self.get_folders(key, output_path, threads_num, first=True)
            else:
                print(f"Invalid link: {mediafire_url}")
                continue

        print("All downloads completed")

    def extract_links_from_file(self, file_path):
        with open(file_path, 'r') as f:
            links = f.readlines()
        links = [link.strip() for link in links if link.strip()]
        return links

    def get_files_or_folders_api_endpoint(
        self, filefolder: str, folder_key: str, chunk: int = 1, info: bool = False
    ) -> str:
        return (
            f"https://www.mediafire.com/api/1.4/folder"
            f"/{'get_info' if info else 'get_content'}.php?r=utga&content_type={filefolder}"
            f"&filter=all&order_by=name&order_direction=asc&chunk={chunk}"
            f"&version=1.5&folder_key={folder_key}&response_format=json"
        )

    def get_info_endpoint(self, file_key: str) -> str:
        return f"https://www.mediafire.com/api/file/get_info.php?quick_key={file_key}&response_format=json"

    def get_folders(
        self, folder_key: str, folder_name: str, threads_num: int, first: bool = False
    ) -> None:
        if first:
            folder_name = os.path.join(
                folder_name,
                self.normalize_file_or_folder_name(
                    requests.get(
                        self.get_files_or_folders_api_endpoint("folder", folder_key, info=True)
                    ).json()["response"]["folder_info"]["name"]
                ),
            )

        if not os.path.exists(folder_name):
            os.makedirs(folder_name)
        os.chdir(folder_name)

        self.download_folder(folder_key, threads_num)

        folder_content = requests.get(
            self.get_files_or_folders_api_endpoint("folders", folder_key)
        ).json()["response"]["folder_content"]

        if "folders" in folder_content:
            for folder in folder_content["folders"]:
                self.get_folders(folder["folderkey"], folder["name"], threads_num)
                os.chdir("..")

    def download_folder(self, folder_key: str, threads_num: int) -> None:
        data = []
        chunk = 1
        more_chunks = True

        try:
            while more_chunks:
                r_json = requests.get(
                    self.get_files_or_folders_api_endpoint("files", folder_key, chunk=chunk)
                ).json()
                more_chunks = r_json["response"]["folder_content"]["more_chunks"] == "yes"
                data += r_json["response"]["folder_content"]["files"]
                chunk += 1

        except KeyError:
            print("Invalid link")
            return

        event = threading.Event()
        threadLimiter = threading.BoundedSemaphore(threads_num)
        total_threads = []

        for file in data:
            total_threads.append(
                threading.Thread(
                    target=self.download_file,
                    args=(
                        file,
                        event,
                        threadLimiter,
                    ),
                )
            )

        for thread in total_threads:
            thread.start()

        try:
            while True:
                if all(not t.is_alive() for t in total_threads):
                    break
                time.sleep(0.01)
        except KeyboardInterrupt:
            print("Closing all threads")
            event.set()
            for thread in total_threads:
                thread.join()
            print("Download interrupted")
            exit(0)

    def download_file(self, file: dict, event: threading.Event = None, limiter: threading.BoundedSemaphore = None) -> None:
        if limiter:
            limiter.acquire()

        download_link = file["links"]["normal_download"]

        filename = self.normalize_file_or_folder_name(file["filename"])

        if os.path.exists(filename):
            if self.hash_file(filename) == file["hash"]:
                print(f"{filename} already exists, skipping")
                if limiter:
                    limiter.release()
                return
            else:
                print(f"{filename} already exists but corrupted, downloading again")

        print(f"Downloading {filename}")

        if event:
            if event.is_set():
                if limiter:
                    limiter.release()
                return

        try:
            if requests.head(download_link).headers.get("content-encoding") == "gzip":
                html = requests.get(download_link).text
                soup = gazpacho.Soup(html)
                download_link = (
                    soup.find("div", {"class": "download_link"})
                    .find("a", {"class": "input popsok"})
                    .attrs["href"]
                )
        except Exception:
            self.print_error(download_link)
            if limiter:
                limiter.release()
            return

        try:
            with requests.get(download_link, stream=True) as r:
                r.raise_for_status()
                with open(filename, "wb") as f:
                    for chunk in r.iter_content(chunk_size=4096):
                        if event:
                            if event.is_set():
                                break
                        if chunk:
                            f.write(chunk)
        except Exception as e:
            print(f"Error downloading {filename}: {str(e)}")
            self.print_error(download_link)
            if limiter:
                limiter.release()
            return

        if event:
            if event.is_set():
                os.remove(filename)
                print(f"Partially downloaded {filename} deleted")
                if limiter:
                    limiter.release()
                return

        print(f"{filename} downloaded")

        if limiter:
            limiter.release()


if __name__ == "__main__":
    downloader = MediafireDownloader()
    try:
        downloader.main()
    except KeyboardInterrupt:
        exit(0)
