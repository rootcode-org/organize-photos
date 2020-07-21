# Copyright is waived. No warranty is provided. Unrestricted use and modification is permitted.

import os
import io
import sys
import shutil
import hashlib
from datetime import datetime, timedelta
import xml.etree.ElementTree as ET

PURPOSE = """
Organize photos by year, month and day of creation

organize-photos.py <path_to_photos> [<path_to_photos>...]

Move and rename photos and videos in the specified path to organize by year, month and day of creation. If
multiple paths are specified then photos from subsequent paths are merged into the first path. 
"""


class FileStream:

    LITTLE_ENDIAN = 0
    BIG_ENDIAN = 1

    def __init__(self, file_name, mode, endian=None):
        self.length = 0
        self.endian = endian if endian else self.LITTLE_ENDIAN
        self.endian_stack = []
        self.handle = io.open(file_name, mode)
        self.length = os.path.getsize(file_name)
        self.position_stack = []

    def close(self):
        self.handle.close()
        self.handle = None

    def get_length(self):
        return self.length

    def set_endian(self, value):
        self.endian = value

    def push_endian(self, new_value = None):
        self.endian_stack.append(self.endian)
        if new_value is not None:
            self.endian = new_value

    def pop_endian(self):
        self.endian = self.endian_stack.pop()

    def set_position(self, position):
        self.handle.seek(position)

    def get_position(self):
        return self.handle.tell()

    def push_position(self, new_position):
        current_position = self.get_position()
        self.position_stack.append(current_position)
        self.set_position(new_position)
        return current_position

    def pop_position(self):
        self.set_position(self.position_stack.pop())

    def is_eof(self):
        return self.handle.tell() == self.length

    def skip(self, num_bytes):
        self.handle.seek(num_bytes, io.SEEK_CUR)

    def read_byte(self):
        return ord(self.handle.read(1))

    def read_bytes(self, length):
        return bytearray(self.handle.read(length))

    def read_short(self):
        if self.endian == self.LITTLE_ENDIAN:
            value = self.read_byte() + (self.read_byte() << 8)
        else:
            value = (self.read_byte() << 8) + self.read_byte()
        return value

    def read_int(self):
        if self.endian == self.LITTLE_ENDIAN:
            value = self.read_short() + (self.read_short() << 16)
        else:
            value = (self.read_short() << 16) + self.read_short()
        return value

    def read_long(self):
        if self.endian == self.LITTLE_ENDIAN:
            value = self.read_int() + (self.read_int() << 32)
        else:
            value = (self.read_int() << 32) + self.read_int()
        return value

    def read_string(self, length):
        value = self.read_bytes(length)
        return value.decode("latin_1")

    # read null terminated string; null character is not returned with string
    def read_nt_string(self):
        output = bytearray()
        value = self.read_byte()
        while value != 0:
            output.append(value)
            value = self.read_byte()
        return output.decode("latin_1")


#############################################################################################################
# Parse file creation date from AVI file
# see https://msdn.microsoft.com/en-us/library/windows/desktop/dd318189(v=vs.85).aspx
# see http://www.sno.phy.queensu.ca/~phil/exiftool/TagNames/RIFF.html
#############################################################################################################

class AVI:
    def __init__(self):
        self.file_path = None
        self.stream = None
        self.chunk_type_stack = []
        self.image_time = None

    def load(self, file_path):
        self.file_path = file_path
        self.stream = FileStream(file_path, "rb", FileStream.LITTLE_ENDIAN)
        signature = self.stream.read_string(4)
        if signature != "RIFF":
            raise ValueError
        file_size = self.stream.read_int()
        file_type = self.stream.read_string(4)
        self.parse_chunks(file_size)

    def parse_chunks(self, end):
        while self.stream.get_position() < end:
            chunk_id = self.stream.read_string(4)
            chunk_size = self.stream.read_int()
            if chunk_id == "LIST":
                list_type = self.stream.read_string(4)
                self.chunk_type_stack.append(list_type)
                self.parse_chunks(self.stream.get_position() + chunk_size)
                self.chunk_type_stack.pop()
            elif chunk_id == "IDIT":
                time_string = self.stream.read_string(chunk_size)[0:-1]
                time_string = time_string.rstrip(" \r\n")
                self.image_time = datetime.strptime(time_string, "%a %b %d %H:%M:%S %Y")
            else:
                self.stream.skip(chunk_size)

    def get_image_time(self):
        return self.image_time


#############################################################################################################
# Parse file creation date from PNG file
# see https://www.w3.org/TR/PNG/
#############################################################################################################

class PNG:
    def __init__(self):
        self.file_path = None
        self.image_time = None

    def load(self, file_path):
        self.file_path = file_path
        stream = FileStream(file_path, "rb", FileStream.BIG_ENDIAN)
        id1 = stream.read_int()
        id2 = stream.read_int()
        if id1 == 0x89504e47 and id2 == 0x0d0a1a0a:
            while not stream.is_eof():
                length = stream.read_int()
                type = stream.read_string(4)
                if type == "tIME":
                    year = stream.read_short()
                    month = stream.read_byte()
                    day = stream.read_byte()
                    hour = stream.read_byte()
                    minute = stream.read_byte()
                    second = stream.read_byte()
                    self.image_time = datetime(year, month, day, hour, minute, second)
                    crc = stream.read_int()
                elif type == "iTXt":                # international text
                    index = stream.get_position()
                    keyword = stream.read_nt_string()
                    compression_flag = stream.read_byte()
                    compression_method = stream.read_byte()
                    language_tag = stream.read_nt_string()
                    translated_keyword = stream.read_nt_string()
                    text_length = length - (stream.get_position() - index)
                    text = stream.read_string(text_length)
                    if keyword == "XML:com.adobe.xmp":
                        xml_root = ET.fromstring(text)
                        date_element = xml_root.find(".//{http://ns.adobe.com/photoshop/1.0/}DateCreated")
                        if date_element is not None:
                            try:
                                self.image_time = datetime.strptime(date_element.text, "%Y-%m-%dT%H:%M:%S")
                            except Exception as e:
                                pass
                    crc = stream.read_int()
                elif type == "IEND":
                    break
                else:
                    stream.skip(length)
                    crc = stream.read_int()

    def get_image_time(self):
        return self.image_time


#############################################################################################################
# Parse file creation date from Quicktime file (.mov, .mp4, .m4v)
# see https://developer.apple.com/library/archive/documentation/QuickTime/QTFF/QTFFChap1/qtff1.html
#############################################################################################################

class Quicktime:
    def __init__(self):
        self.stream = None
        self.type_stack = []
        self.image_time = None

    def load(self, url):
        self.stream = FileStream(url, "rb", FileStream.BIG_ENDIAN)
        self.parse(self.stream.get_length())

    def parse(self, end):
        while self.stream.get_position() < end:
            atom_size = self.stream.read_int()
            if atom_size == 0:                      # Size of 0 indicates this is the last atom
                break
            atom_type = self.stream.read_string(4)
            if atom_size == 1:                      # size of 1 indicates a 64-bit size follows the type
                atom_size = self.stream.read_long() - 16    # reduce by length of size and type fields
            else:
                atom_size -= 8      # reduce by length of size and type fields

            if atom_type in ["moov"]:
                # Recursively parse into this atom type
                self.type_stack.append(atom_type)
                self.parse(self.stream.get_position() + atom_size)
                self.type_stack.pop()

            elif atom_type == "mvhd":
                # The Movie Header atom contains a creation date
                self.version = self.stream.read_byte()
                self.flags = self.stream.read_bytes(3)
                self.creation_time = self.stream.read_int()
                self.modification_time = self.stream.read_int()
                self.time_scale = self.stream.read_int()
                self.duration = self.stream.read_int()
                self.preferred_rate = self.stream.read_int()
                self.preferred_volume = self.stream.read_short()
                self.stream.skip(10)        # reserved
                self.matrix = self.stream.read_bytes(36)
                self.preview_time = self.stream.read_int()
                self.preview_duration = self.stream.read_int()
                self.poster_time = self.stream.read_int()
                self.selection_time = self.stream.read_int()
                self.selection_duration = self.stream.read_int()
                self.current_time = self.stream.read_int()
                self.next_track_id = self.stream.read_int()

                # Convert the creation time to a datetime object
                mac_unix_epoch_diff = 2082844800        # Difference in seconds between mac and unix epoch times
                timestamp = self.creation_time - mac_unix_epoch_diff
                self.image_time = datetime.utcfromtimestamp(timestamp)

            elif atom_type == "udta":
                # The udta type contains a list of user data types, one of which can contain the creation date
                list_start = self.stream.get_position()
                list_end = list_start + atom_size
                while self.stream.get_position() < list_end:
                    atom_size = self.stream.read_int()
                    if atom_size == 0:
                        break
                    atom_type = self.stream.read_string(4)
                    atom_size -= 8      # reduce by length of size and type fields

                    if atom_type == "\xa9day":
                        data_size = self.stream.read_short()
                        data_language = self.stream.read_short()
                        time_string = self.stream.read_string(data_size)[0:19]
                        try:
                            self.image_time = datetime.strptime(time_string, "%Y-%m-%dT%H:%M:%S")
                        except Exception as e:
                            pass
                    else:
                        self.stream.skip(atom_size)

            # All other types are skipped
            else:
                self.stream.skip(atom_size)

    def get_image_time(self):
        return self.image_time


#############################################################################################################
# Parse file creation date from TIFF file
# see http://www.fileformat.info/format/tiff/egff.htm
# see http://www.sno.phy.queensu.ca/~phil/exiftool/TagNames/EXIF.html
#############################################################################################################

class TIFF:
    def __init__(self):
        self.stream = None
        self.ifd_start = 0
        self.image_time = None

    def init(self, stream):
        self.stream = stream

    def load(self, url):
        self.stream = FileStream(url, "rb")
        self.parse()

    def parse(self):
        # All IFD offsets are relative to this position
        self.ifd_start = self.stream.get_position()

        # Determine the file byte order
        byte_order = self.stream.read_short()
        if byte_order == 0x4949:
            self.stream.set_endian(self.stream.LITTLE_ENDIAN)
        elif byte_order == 0x4d4d:
            self.stream.set_endian(self.stream.BIG_ENDIAN)
        else:
            raise ValueError

        # Check signature value
        fortytwo = self.stream.read_short()
        if fortytwo != 42:
            raise ValueError

        # Now we get the offset to the first IFD
        ifd_offset = self.stream.read_int()
        self.stream.set_position(self.ifd_start + ifd_offset)

        # Parse IFD's
        next_ifd = self.parse_ifd()
        while next_ifd != 0:
            self.stream.set_position(self.ifd_start + next_ifd)
            next_ifd = self.parse_ifd()

    def parse_ifd(self):
        num_entries = self.stream.read_short()
        for i in range(num_entries):
            tag = self.stream.read_short()
            type = self.stream.read_short()
            count = self.stream.read_int()
            offset = self.ifd_start + self.stream.read_int()

            # This tag provides an offset to another IFD
            if tag == 0x8769:             # ExifOffset
                self.stream.push_position(offset)
                self.parse_ifd()
                self.stream.pop_position()

            # If tag is one of ModifyDate, DateTimeOriginal or CreateDate then attempt to extract a timestamp
            elif tag in [0x0132, 0x9003, 0x9004]:
                self.stream.push_position(offset)
                time_string = self.stream.read_string(count - 1)
                self.stream.pop_position()
                if time_string[0:4] != "0000":
                    try:
                        self.image_time = datetime.strptime(time_string, "%Y:%m:%d %H:%M:%S")
                    except Exception as e:
                        # Sometimes dates can be malformed, e.g. Feb 29 in a non-leap year. Attempt to handle this.
                        try:
                            dt = datetime.strptime(time_string[0:7], "%Y:%m")
                            days = int(time_string[8:10])
                            delta = timedelta(days-1)
                            self.image_time = dt + delta
                        except Exception as f:
                            pass
            else:
                pass

        next_ifd = self.stream.read_int()
        return next_ifd

    def get_image_time(self):
        return self.image_time


#############################################################################################################
# Parse file creation date from JPEG file
# see https://en.wikipedia.org/wiki/JPEG
# see http://www.ozhiker.com/electronics/pjmt/jpeg_info/app_segments.html
# see http://www.exif.org/Exif2-2.PDF
# see https://www.sno.phy.queensu.ca/~phil/exiftool/TagNames/FlashPix.html
#############################################################################################################

class JPEG:
    def __init__(self):
        self.image_time = None

    def load(self, file_path):
        stream = FileStream(file_path, "rb", FileStream.BIG_ENDIAN)
        while not stream.is_eof():
            marker = stream.read_short()

            # start of image marker
            if marker == 0xffd8:
                pass

            # app1/app3 markers
            elif marker == 0xffe1 or marker == 0xffe3:
                length = stream.read_short() - 2
                position = stream.get_position()

                signature = stream.read_string(4)
                if signature == "Exif" or signature == "Meta":
                    stream.skip(2)
                    stream.push_endian()
                    t = TIFF()
                    t.init(stream)
                    t.parse()
                    stream.pop_endian()
                    stream.set_position(position + length)
                    if not self.image_time:
                        self.image_time = t.get_image_time()

                # Adobe 'http' metadata or 'XMP\x00' metadata
                elif signature == "http" or signature == "XMP\x00":
                    url_string = stream.read_nt_string()
                    text_length = length - len(url_string) - 5
                    text = stream.read_string(text_length)
                    text = text.rstrip(" \r\n\x00")
                    xml_root = ET.fromstring(text)
                    element = xml_root.find(".//{http://www.w3.org/1999/02/22-rdf-syntax-ns#}Description[@{http://ns.adobe.com/exif/1.0/}DateTimeOriginal]")
                    if element is not None:
                        timestamp = element.attrib["{http://ns.adobe.com/exif/1.0/}DateTimeOriginal"][0:19]
                        self.image_time = datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%S")
                else:
                    raise ValueError

            # app13 marker (Adobe IRB)
            elif marker == 0xffed:
                length = stream.read_short() - 2

                # Parse IRB blocks
                # See 'Image Resource Blocks' in http://www.adobe.com/devnet-apps/photoshop/fileformatashtml/
                irb_end = stream.get_position() + length
                photoshop_version = stream.read_nt_string()
                while stream.get_position() < irb_end:
                    irb_signature = stream.read_string(4)
                    if irb_signature != "8BIM":
                        raise ValueError

                    resource_type = stream.read_short()
                    resource_name_length = stream.read_byte()
                    resource_name = stream.read_string(resource_name_length)
                    if (resource_name_length & 1) == 0:
                        stream.skip(1)
                    resource_data_length = stream.read_int()

                    if resource_type == 0x404:
                        # IPTC-NAA Record; See https://www.iptc.org/std/IIM/4.1/specification/IIMV4.1.pdf
                        # N.B. this record can be shorter than the resource_data_length specified; it appears the
                        # resource length is padded to the next word boundary
                        iptc_end = stream.get_position() + resource_data_length
                        while stream.get_position() < iptc_end - 3:
                            tag_marker = stream.read_byte()
                            record_number = stream.read_byte()
                            data_set_number = stream.read_byte()
                            data_field_count = stream.read_short()

                            # Any of these record types can contain a date
                            # 1:70 (Date Sent), 2:30 (Release Date), 2:55 (Date Created), 2:62 (Digital Creation Date)
                            if (record_number == 1 and data_set_number == 70) \
                                    or (record_number == 2 and data_set_number == 30) \
                                    or (record_number == 2 and data_set_number == 55) \
                                    or (record_number == 2 and data_set_number == 62):
                                date_string = stream.read_string(data_field_count)
                                self.image_time = datetime.strptime(date_string, "%Y%m%d")
                            else:
                                stream.skip(data_field_count)

                        # Adjust the stream position since it may not be in the correct place due to the IPTC
                        # record being shorter than actually specified in the resource length
                        stream.set_position(iptc_end)
                    else:
                        stream.skip(resource_data_length)

                    # Resources are always padded to the next 16-bit boundary
                    if (resource_data_length & 1) == 1:
                        stream.skip(1)

            # start of scan marker (ends image)
            elif marker == 0xffda:
                break

            # end of image marker
            elif marker == 0xffd9:
                break

            # skip any other markers
            else:
                length = stream.read_short() - 2
                stream.skip(length)

    def get_image_time(self):
        return self.image_time


#############################################################################################################
# Entry Point
#############################################################################################################

if __name__ == '__main__':

    if len(sys.argv) < 2:
        sys.exit(PURPOSE)

    # The first path is the collection into which files from all other paths are merged
    collection_path = os.path.expanduser(sys.argv[1]).replace("\\", "/")
    all_paths = sys.argv[1:]        # includes collection path
    collection_checksums = {}

    # Merge files from all paths into collection
    for next_path in all_paths:
        image_files = []
        next_path = os.path.expanduser(next_path).replace("\\", "/")
        if not os.path.exists(next_path):
            sys.exit("ERROR: Path does not exist; " + next_path)
        for path, folders, files in os.walk(next_path):
            for file_name in files:
                full_path = os.path.join(path, file_name).replace("\\", "/")
                base_name, ext = os.path.splitext(file_name)
                ext = ext.lower()

                # If the creation date is in the file name then this is considered the authoritative date
                image_time = None
                formats = [("%Y-%m-%d_%H%M%S", 17), ("%Y-%m-%d", 10), ("%Y-%m", 7), ("IMG-%Y%m%d", 12), ("VID_%Y%m%d", 12)]
                for format, length in formats:
                    try:
                        image_time = datetime.strptime(file_name[:length], format)
                        break
                    except ValueError:
                        pass

                # Attempt to parse the creation date from the image file metadata
                if not image_time:
                    if ext in [".jpg", ".jpeg"]:
                        image = JPEG()
                        image.load(full_path)
                        image_time = image.get_image_time()
                    elif ext in [".mov", ".mp4", ".m4v"]:
                        image = Quicktime()
                        image.load(full_path)
                        image_time = image.get_image_time()
                    elif ext == ".png":
                        image = PNG()
                        image.load(full_path)
                        image_time = image.get_image_time()
                    elif ext in [".tif", ".tiff"]:
                        image = TIFF()
                        image.load(full_path)
                        image_time = image.get_image_time()
                    elif ext in [".avi", ".mpg", ".mpeg"]:
                        image = AVI()
                        image.load(full_path)
                        image_time = image.get_image_time()
                    elif ext in [".bmp"]:           # These image files don't contain an embedded creation date
                        pass
                    else:
                        # Not a supported image type so skip file
                        continue

                # Last resort; use file modification time as the image time
                if not image_time:
                    image_time = datetime.fromtimestamp(os.path.getmtime(full_path))

                # add to list of located image files
                image_files.append((full_path, image_time))

        # Move image files
        image_files.sort(key=lambda x: x[1])       # sort on timestamp
        for full_path, image_time in image_files:

            # Checksum file; skip if already in collection
            with open(full_path, "rb") as f:
                file_checksum = hashlib.sha256(f.read()).digest()
            if file_checksum in collection_checksums:
                continue
            collection_checksums[file_checksum] = full_path

            # Create destination path
            file_folder, file_name = os.path.split(full_path)
            base_name, ext = os.path.splitext(file_name)
            ext = ext.lower()
            if ext == ".jpeg": ext = ".jpg"
            if ext == ".tiff": ext = ".tif"
            checksum_hex = file_checksum[0:10].hex()
            dst_name = "{0}-{1:02}-{2:02}_{3:02}{4:02}{5:02}_{6}".format(image_time.year, image_time.month, image_time.day, image_time.hour, image_time.minute, image_time.second, checksum_hex)
            dst_path = os.path.join(collection_path, str(image_time.year), "{0}-{1:02}".format(image_time.year, image_time.month))
            dst = os.path.join(dst_path, dst_name + ext).replace("\\", "/")

            # If file is already in correct place then skip
            if full_path == dst:
                continue

            # Create the parent folders for the file
            folder_name = os.path.split(dst)[0]
            if not os.path.exists(folder_name):
                try:
                    os.makedirs(folder_name)
                except os.error:
                    print("ERROR: Unable to create " + folder_name)
                    continue

            # Move the file
            print ("Moving {0} --> {1}".format(full_path, dst))
            shutil.move(full_path,dst)

    # Remove empty folders from collection
    empty_folders = []
    for path, folders, files in os.walk(collection_path):
        if len(folders) + len(files) == 0:
            empty_folders.append(path)
    for folder in empty_folders:
        print ("Removing empty folder " + folder)
        os.rmdir(folder)
