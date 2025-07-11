import dataclasses
import io
import os
import re
import zipfile
from uuid import uuid4
from xml.etree.ElementTree import Element, SubElement, tostring

import yaml
from pdf2image import convert_from_path
from PIL.Image import Image

HTML_PAGE_TEMPLATE = """
<html xmlns="http://www.w3.org/1999/xhtml" style="display: flex; justify-content: center; width: 100vw; height: 100%;">
    <head>
        <title>{chapter_title} - Page {page_number}</title>
        <style>
           img {{
                display: block;
                margin: 0 auto;
                width: 100%;
                height: auto;
                max-height: none !important;
              }}
      </style>

    </head>
    <body style="display: flex; justify-content: center; width: 70vw; height: 100%;">
       <div style="display: block; margin: auto; width: 100%; height: 100%; overflow: scroll;">
          <img id="{image_url}" src="{image_url}" style="max-width: 100%; height: auto; display: block; margin: 0 auto; max-height: unset !important; width: 100%;" alt="Page image" onload="init()"/>
        </div>

        <script>
         function init(){{
          const id = "{image_url}";
          const image = document.getElementById(id);

          image.style.maxHeight = "unset !important"
          

         }}
        </script>
    </body>
</html>
"""

MIMETYPE = "application/epub+zip"
CONTAINER_XML = """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""

IMAGE_DPI = 150


@dataclasses.dataclass
class ChapterTOC:
    page_ids: list[str]
    chapter_title: str


@dataclasses.dataclass
class ManifestItem:
    id: str
    href: str
    media_type: str = "application/xhtml+xml"

    def to_xml_content_dict(self):
        return {"id": self.id, "href": self.href, "media-type": self.media_type}


class ChapterData:
    def __init__(self, name: str, path: str) -> None:
        self.name = name
        self.path = path
        self.chapter_number = int(
            re.search(
                r"\d+", self.name.replace("Ch", "").replace(".pdf", "").strip()
            ).group()
        )

        self.chapter_title = f"Chapter {self.chapter_number}"

    @staticmethod
    def create_png_img(img: Image):
        buffer = io.BytesIO()
        img.save(buffer, format="PNG", optimize=True)

        return buffer.getvalue()

    def create_chapter(self, epub_file: zipfile.ZipFile):
        manifest_items: list[ManifestItem] = []
        images = convert_from_path(self.path, dpi=IMAGE_DPI)
        page_ids = []

        print(f"creating chapter: {self.chapter_title}, with {len(images)} images")

        for index, image in enumerate(images):
            page_number = index + 1
            page_id = f"chapter_{self.chapter_number}_page_{page_number}".replace(
                " ", ""
            )

            file_name = f"{page_id}.xhtml"
            image_url = f"static/{page_id}.png"
            file_title = f"{self.chapter_title} - Page {page_number}"

            print(f"creating: {file_title}")

            content = HTML_PAGE_TEMPLATE.format(
                chapter_title=self.chapter_title,
                page_number=page_number,
                image_url=image_url,
            )

            xhtml_path = f"OEBPS/{file_name}"
            image_path = f"OEBPS/{image_url}"

            epub_file.writestr(xhtml_path, content, compress_type=zipfile.ZIP_DEFLATED)

            epub_file.writestr(
                image_path,
                self.create_png_img(image),
                compress_type=zipfile.ZIP_DEFLATED,
            )

            page_ids.append(page_id)

            manifest_item = ManifestItem(id=page_id, href=file_name)

            manifest_items.append(manifest_item)

            img_manifest_item = ManifestItem(
                id=f"{page_id}_img", href=image_url, media_type="image/png"
            )

            manifest_items.append(img_manifest_item)

        return manifest_items, page_ids


class EbookXMLManager:
    def __init__(self, book_id: str, name: str):
        self.package = self.create_package(book_id)
        self.metadata = self.create_metadata(self.package, name, book_id)
        self.manifest = SubElement(self.package, "manifest")
        self.spine = SubElement(self.package, "spine", {"toc": "ncx"})
        self.ncx = self.build_ncx(name, book_id)

    @staticmethod
    def create_package(book_id: str) -> Element:
        package = Element("package")
        package.set("xmlns", "http://www.idpf.org/2007/opf")
        package.set("version", "2.0")
        package.set("unique-identifier", book_id)

        return package

    @staticmethod
    def create_metadata(package: Element, title: str, book_id: str):
        metadata = SubElement(package, "metadata")
        metadata.set("xmlns:dc", "http://purl.org/dc/elements/1.1/")
        metadata.set("xmlns:opf", "http://www.idpf.org/2007/opf")

        SubElement(metadata, "dc:title").text = title
        SubElement(metadata, "dc:language").text = "en"
        SubElement(metadata, "dc:identifier", id=book_id).text = book_id

        return metadata

    def create_manifest_item(self, data: dict):
        SubElement(self.manifest, "item", data)

    def build_manifest_data(
        self, manifest_items: list[ManifestItem], spine_items: list[str]
    ) -> str:

        self.create_manifest_item(
            {"id": "ncx", "href": "toc.ncx", "media-type": "application/x-dtbncx+xml"}
        )

        for manifest_item in manifest_items:
            self.create_manifest_item(manifest_item.to_xml_content_dict())

        for item_id in spine_items:
            SubElement(self.spine, "itemref", {"idref": item_id})

        return '<?xml version="1.0"?>\n' + tostring(self.package, encoding="unicode")

    @staticmethod
    def build_ncx(title, book_id: str) -> Element:
        ncx = Element(
            "ncx",
            {"xmlns": "http://www.daisy.org/z3986/2005/ncx/", "version": "2005-1"},
        )

        head = SubElement(ncx, "head")
        SubElement(head, "meta", {"name": "dtb:uid", "content": book_id})

        doc_title = SubElement(ncx, "docTitle")
        SubElement(doc_title, "text").text = title

        return ncx

    def build_ncx_data(self, toc: list[ChapterTOC]) -> str:
        nav_map = SubElement(self.ncx, "navMap")

        play_order = 1

        for chapter_index, chapter_toc in enumerate(toc):
            first_page_url = f"{chapter_toc.page_ids[0]}.xhtml"
            nav_point = SubElement(
                nav_map,
                "navPoint",
                {"id": f"navPoint-{chapter_index + 1}", "playOrder": str(play_order)},
            )

            nav_label = SubElement(nav_point, "navLabel")

            SubElement(nav_label, "text").text = chapter_toc.chapter_title
            SubElement(nav_point, "content", {"src": first_page_url})
            play_order += 1

        return '<?xml version="1.0" encoding="UTF-8"?>\n' + tostring(
            self.ncx, encoding="unicode"
        )


class EbookManager:
    def __init__(self, name: str, pdfs_path: str):
        self.name = name
        self.pdfs_path = pdfs_path
        self.book_id = uuid4().hex
        self.spine = []
        self.xml_manager = EbookXMLManager(self.book_id, self.name)
        self.toc: list[ChapterTOC] = []
        self.manifest_items: list[ManifestItem] = []
        self.output_name = f"{self.name}.epub"
        self.pdf_files = self.init_pdf_list()

    @staticmethod
    def chapter_sorting(value: ChapterData):
        return int(re.search(r"\d+", value.name).group())

    def init_pdf_list(self):
        pdf_files: list[ChapterData] = []
        files = os.listdir(self.pdfs_path)

        for pdf_file in files:
            if not pdf_file.endswith(".pdf"):
                continue

            chapter_data = ChapterData(pdf_file, os.path.join(self.pdfs_path, pdf_file))

            pdf_files.append(chapter_data)

        pdf_files = sorted(pdf_files, key=self.chapter_sorting)

        return pdf_files

    def handle_chapter(self, chapter: ChapterData, epub_file: zipfile.ZipFile):
        manifest_items, page_ids = chapter.create_chapter(epub_file)

        for manifest_item in manifest_items:
            self.manifest_items.append(manifest_item)

        for page_id in page_ids:
            self.spine.append(page_id)

        chapter_toc = ChapterTOC(page_ids=page_ids, chapter_title=chapter.chapter_title)

        self.toc.append(chapter_toc)

    @staticmethod
    def add_mime_type(file: zipfile.ZipFile):
        file.writestr("mimetype", MIMETYPE, compress_type=zipfile.ZIP_STORED)

    @staticmethod
    def add_container(file: zipfile.ZipFile):
        file.writestr("META-INF/container.xml", CONTAINER_XML)

    @staticmethod
    def add_manifest(file: zipfile.ZipFile, manifest_data: str):
        file.writestr(
            "OEBPS/content.opf", manifest_data, compress_type=zipfile.ZIP_DEFLATED
        )

    @staticmethod
    def add_navigation(file: zipfile.ZipFile, ncx_data: str):
        file.writestr("OEBPS/toc.ncx", ncx_data, compress_type=zipfile.ZIP_DEFLATED)

    def build(self, epub_file: zipfile.ZipFile):
        ncx_data = self.xml_manager.build_ncx_data(self.toc)
        manifest_data = self.xml_manager.build_manifest_data(
            self.manifest_items, self.spine
        )

        self.add_manifest(epub_file, manifest_data)
        self.add_navigation(epub_file, ncx_data)

    def create_ebook(self):
        print(f"create_ebook: {self.name}")

        with zipfile.ZipFile(self.output_name, "w") as epub_file:
            self.add_mime_type(epub_file)
            self.add_container(epub_file)

            for chapter in self.pdf_files:
                self.handle_chapter(chapter, epub_file)

            self.build(epub_file)
            print(f"✅ EPUB created: {self.output_name}")


def get_config_file() -> dict:
    config: dict | None = None

    with open("config.yaml", "r") as file:
        config = yaml.safe_load(file)

    if not config:
        raise Exception("Can't load config")

    if not config.get("ebooks"):
        raise Exception("Can't find ebooks section data")

    return config


def start():
    config = get_config_file()

    for name, ebook_def in config.get("ebooks").items():
        path = ebook_def.get("path")

        if not path:
            print(f"skipping {name}, path is missing")

            continue

        absolute_path = os.path.abspath(path)

        if not os.path.exists(absolute_path):
            print(f"skipping {name}, path doesn't exists")

            continue

        ebook_manager = EbookManager(name, absolute_path)
        ebook_manager.create_ebook()


if __name__ == "__main__":
    start()
