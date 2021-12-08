# Copyright is waived. No warranty is provided. Unrestricted use and modification is permitted.

import os
import io
import sys
import datetime
import hashlib
import shutil
import xml.etree.ElementTree as ET

PURPOSE = """
Organize photos and videos by time of creation

organize-photos.py <path_to_photos> [<path_to_photos>...]

Move and rename photos and videos in the specified path to organize by year, month and day of creation. If
multiple paths are specified then photos from subsequent paths are merged into the first path. 
"""


class FileStream:

    LITTLE_ENDIAN = 0
    BIG_ENDIAN = 1

    def __init__(self, file_name, mode):
        self.length = 0
        self.endian = self.LITTLE_ENDIAN
        self.endian_stack = []
        self.handle = io.open(file_name, mode)
        self.length = os.path.getsize(file_name)
        self.position_stack = []

    def close(self):
        self.handle.close()

    def set_position(self, position, whence=io.SEEK_SET):
        self.handle.seek(position, whence)

    def get_position(self):
        return self.handle.tell()

    def push_position(self, new_position):
        current_position = self.get_position()
        self.position_stack.append(current_position)
        self.set_position(new_position)
        return current_position

    def pop_position(self):
        self.set_position(self.position_stack.pop())

    def get_remaining(self):
        return self.length - self.handle.tell()

    def is_eof(self):
        return self.handle.tell() == self.length

    def flush(self):
        self.handle.flush()

    def get_length(self):
        return self.length

    def set_endian(self, value):
        self.endian = value

    def get_endian(self):
        return self.endian

    def push_endian(self, new_value=None):
        self.endian_stack.append(self.endian)
        if new_value is not None:
            self.endian = new_value

    def pop_endian(self):
        self.endian = self.endian_stack.pop()

    def read_u8(self):
        return ord(self.handle.read(1))

    def read_u8_array(self, length):
        return bytearray(self.handle.read(length))

    def read_bool(self):
        value = self.read_u8()
        return False if value == 0 else True

    def read_u16(self):
        if self.endian == self.LITTLE_ENDIAN:
            value = self.read_u8() + (self.read_u8() << 8)
        else:
            value = (self.read_u8() << 8) + self.read_u8()
        return value

    def read_u24(self):
        if self.endian == self.LITTLE_ENDIAN:
            value = self.read_u8() + (self.read_u8() << 8) + (self.read_u8() << 16)
        else:
            value = (self.read_u8() << 16) + (self.read_u8() << 8) + self.read_u8()
        return value

    def read_u32(self):
        if self.endian == self.LITTLE_ENDIAN:
            value = self.read_u16() + (self.read_u16() << 16)
        else:
            value = (self.read_u16() << 16) + self.read_u16()
        return value

    def read_u64(self):
        if self.endian == self.LITTLE_ENDIAN:
            value = self.read_u32() + (self.read_u32() << 32)
        else:
            value = (self.read_u32() << 32) + self.read_u32()
        return value

    def read_string(self, length):
        value = self.read_u8_array(length)
        return value.decode("latin_1")

    # null terminated string; null character is not returned with string
    def read_nt_string(self):
        output = bytearray()
        value = self.read_u8()
        while value != 0:
            output.append(value)
            value = self.read_u8()
        return output.decode("latin_1")


###############################################################################################################
# AVI format
###############################################################################################################

# For AVI format see https://msdn.microsoft.com/en-us/library/windows/desktop/dd318189(v=vs.85).aspx
# For RIFF tags see http://www.sno.phy.queensu.ca/~phil/exiftool/TagNames/RIFF.html

class AVI:
    def __init__(self):
        self.file_path = None
        self.stream = None
        self.chunk_type_stack = []
        self.image_time = None

    def load(self, file_path):
        self.file_path = file_path
        self.stream = FileStream(file_path, "rb")
        self.stream.set_endian(self.stream.LITTLE_ENDIAN)
        signature = self.stream.read_string(4)
        if signature != "RIFF":
            raise ValueError
        file_size = self.stream.read_u32()
        file_type = self.stream.read_string(4)
        self.parse_chunks(file_size)

    def parse_chunks(self, end):
        while self.stream.get_position() < end:
            chunk_id = self.stream.read_string(4)
            chunk_size = self.stream.read_u32()
            if chunk_id == "LIST":
                list_type = self.stream.read_string(4)
                self.chunk_type_stack.append(list_type)
                self.parse_chunks(self.stream.get_position() + chunk_size)
                self.chunk_type_stack.pop()
            elif chunk_id == "IDIT":
                time_string = self.stream.read_string(chunk_size)[0:-1]
                time_string = time_string.rstrip(" \r\n")
                self.image_time = datetime.datetime.strptime(time_string, "%a %b %d %H:%M:%S %Y")
            else:
                self.stream.set_position(chunk_size, io.SEEK_CUR)

    def get_image_time(self):
        return self.image_time


###############################################################################################################
# JPEG format
###############################################################################################################

# For JPEG format see https://en.wikipedia.org/wiki/JPEG
# For app segments see http://www.ozhiker.com/electronics/pjmt/jpeg_info/app_segments.html
# For EXIF format see http://www.exif.org/Exif2-2.PDF

class JPEG:
    def __init__(self):
        self.file_path = None
        self.exif = None
        self.image_time = None

    def load(self, file_path):
        self.file_path = file_path
        stream = FileStream(file_path, "rb")
        stream.set_endian(stream.BIG_ENDIAN)
        while not stream.is_eof():
            marker = stream.read_u16()

            # start of image marker
            if marker == 0xffd8:
                pass

            # app1/app3 markers
            elif marker == 0xffe1 or marker == 0xffe3:
                length = stream.read_u16() - 2
                position = stream.get_position()

                signature = stream.read_string(4)
                if signature == "Exif" or signature == "Meta":
                    stream.set_position(2, io.SEEK_CUR)
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
                        self.image_time = datetime.datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%S")
                else:
                    raise ValueError

            # app13 marker (Adobe IRB)
            elif marker == 0xffed:
                length = stream.read_u16() - 2

                # Parse IRB blocks
                # See 'Image Resource Blocks' in http://www.adobe.com/devnet-apps/photoshop/fileformatashtml/
                irb_end = stream.get_position() + length
                photoshop_version = stream.read_nt_string()
                while stream.get_position() < irb_end:
                    irb_signature = stream.read_string(4)
                    if irb_signature != "8BIM":
                        raise ValueError

                    resource_type = stream.read_u16()
                    resource_name_length = stream.read_u8()
                    resource_name = stream.read_string(resource_name_length)
                    if (resource_name_length & 1) == 0:
                        stream.set_position(1, io.SEEK_CUR)
                    resource_data_length = stream.read_u32()

                    if resource_type == 0x404:
                        # IPTC-NAA Record; See https://www.iptc.org/std/IIM/4.1/specification/IIMV4.1.pdf
                        # N.B. this record can be shorter than the resource_data_length specified; it appears the
                        # resource length is padded to the next word boundary
                        iptc_end = stream.get_position() + resource_data_length
                        while stream.get_position() < iptc_end - 3:
                            tag_marker = stream.read_u8()
                            record_number = stream.read_u8()
                            data_set_number = stream.read_u8()
                            data_field_count = stream.read_u16()

                            # Any of these record types can contain a date
                            # 1:70 (Date Sent), 2:30 (Release Date), 2:55 (Date Created), 2:62 (Digital Creation Date)
                            if (record_number == 1 and data_set_number == 70) \
                                    or (record_number == 2 and data_set_number == 30) \
                                    or (record_number == 2 and data_set_number == 55) \
                                    or (record_number == 2 and data_set_number == 62):
                                date_string = stream.read_string(data_field_count)
                                self.image_time = datetime.datetime.strptime(date_string, "%Y%m%d")
                            else:
                                stream.set_position(data_field_count, io.SEEK_CUR)

                        # Adjust the stream position since it may not be in the correct place due to the IPTC
                        # record being shorter than actually specified in the resource length
                        stream.set_position(iptc_end)
                    else:
                        stream.set_position(resource_data_length, io.SEEK_CUR)

                    # Resources are always padded to the next 16-bit boundary
                    if (resource_data_length & 1) == 1:
                        stream.set_position(1, io.SEEK_CUR)

            # start of scan marker
            elif marker == 0xffda:
                length = stream.read_u16() - 2
                self.scan_header = stream.read_u8_array(length)
                # Assume this marker is the last marker except for the end of image marker
                length = stream.get_length() - stream.get_position() - 2
                self.scan_data = stream.read_u8_array(length)
                break

            # end of image marker
            elif marker == 0xffd9:
                break

            # any other markers are unhandled for now
            else:
                length = stream.read_u16() - 2
                stream.set_position(length, io.SEEK_CUR)

    def get_image_time(self):
        return self.image_time


###############################################################################################################
# MP4 format
###############################################################################################################

# ISO/IEC Base Media file format is described here;
#   https://mpeg.chiariglione.org/standards/mpeg-4/iso-base-media-file-format/text-isoiec-14496-12-5th-edition
#
# Atom types and specification owner are listed at http://mp4ra.org/#/atoms
#
# HEIF file format is described here;
#   https://mpeg.chiariglione.org/standards/mpeg-h/image-file-format/text-isoiec-cd-23008-12-image-file-format

class MP4:
    def __init__(self):
        self.url = None
        self.stream = None
        self.image_time = None
        self.exif_id = None

    def load(self, url):
        self.url = url
        self.stream = FileStream(url, 'rb')
        self.stream.set_endian(self.stream.BIG_ENDIAN)
        self.parse(self.stream.get_length())

    # Parse one or more sequential atoms and try to locate image creation time
    def parse(self, end_position):
        while self.stream.get_position() < end_position:
            # Parse the atom header
            atom_size = self.stream.read_u32()
            if atom_size == 0:                      # Size of 0 means 'parse to end of file'
                continue                            # We can ignore because we parse to the end of the atom list anyway
            atom_type = self.stream.read_string(4)
            atom_size = self.stream.read_u64() if atom_size == 1 else atom_size
            atom_type = self.stream.read_u8_array(8) if atom_type == 0x75756964 else atom_type
            atom_version = 0
            atom_flags = 0

            # These atoms are containers for other atoms
            if atom_type in ['moov', 'udta', 'meta']:
                self.parse(self.stream.get_position() + atom_size - 8)

            # Parse Movie Header atom
            elif atom_type == 'mvhd':
                self.version = self.stream.read_u8()
                self.flags = self.stream.read_u8_array(3)
                self.creation_time = self.stream.read_u32()         # this is what we're looking for
                self.modification_time = self.stream.read_u32()
                self.time_scale = self.stream.read_u32()
                self.duration = self.stream.read_u32()
                self.preferred_rate = self.stream.read_u32()
                self.preferred_volume = self.stream.read_u16()
                self.stream.set_position(10, io.SEEK_CUR)        # skip reserved bytes
                self.matrix = self.stream.read_u8_array(36)
                self.preview_time = self.stream.read_u32()
                self.preview_duration = self.stream.read_u32()
                self.poster_time = self.stream.read_u32()
                self.selection_time = self.stream.read_u32()
                self.selection_duration = self.stream.read_u32()
                self.current_time = self.stream.read_u32()
                self.next_track_id = self.stream.read_u32()

                # Convert the creation time to a datetime object
                if self.creation_time != 0:
                    mac_unix_epoch_diff = 2082844800        # Difference in seconds between mac and unix epoch times
                    timestamp = self.creation_time - mac_unix_epoch_diff
                    self.image_time = datetime.datetime.utcfromtimestamp(timestamp)

            # Parse iTunes metadata
            elif atom_type == '\xa9day':
                data_size = self.stream.read_u16()
                data_language = self.stream.read_u16()
                time_string = self.stream.read_string(data_size)[0:19]
                try:
                    self.image_time = datetime.datetime.strptime(time_string, '%Y-%m-%dT%H:%M:%S')
                except ValueError:
                    pass

            # Parse Item Information Box (found in Apple HEIC files)
            elif atom_type == 'iinf':
                atom_version = self.stream.read_u8()
                atom_flags = self.stream.read_u24()
                if atom_version == 0:
                    item_count = self.stream.read_u16()
                    self.parse(self.stream.get_position() + atom_size - 14)
                else:
                    item_count = self.stream.read_u32()
                    self.parse(self.stream.get_position() + atom_size - 16)

            # Parse Item Information Entry (found in Apple HEIC files)
            # Here we're looking for the index to the Exif data, which we will then look up in the 'iloc' atom
            elif atom_type == 'infe':
                atom_version = self.stream.read_u8()
                atom_flags = self.stream.read_u24()
                if atom_version == 2:
                    item_id = self.stream.read_u16()
                    item_index = self.stream.read_u16()
                    item_type = self.stream.read_string(4)
                    item_name = self.stream.read_nt_string()
                    if item_type == 'Exif':
                        self.exif_id = item_id
                    else:
                        self.stream.set_position(atom_size - 21, whence=io.SEEK_CUR)
                else:
                    sys.exit('Unsupported INFE atom version')

            # Parse Item Location Box (found in Apple HEIC files)
            elif atom_type == 'iloc':
                atom_version = self.stream.read_u8()
                atom_flags = self.stream.read_u24()
                offset_size = self.stream.read_u8()
                length_size = offset_size & 0x0f
                offset_size >>= 4
                base_offset_size = self.stream.read_u8()
                index_size = base_offset_size & 0x0f
                base_offset_size >>= 4
                item_count = self.stream.read_u16() if atom_version < 2 else self.stream.read_u32()
                extent_offset = extent_length = 0
                for i in range(item_count):
                    item_id = self.stream.read_u16() if atom_version < 2 else self.stream.read_u32()
                    if atom_version == 1 or atom_version == 2:
                        construction_method = self.stream.read_u16() & 0x000f
                    else:
                        construction_method = 0
                    data_reference_index = self.stream.read_u16()
                    if base_offset_size > 0:
                        base_offset = self.stream.read_u32() if base_offset_size == 4 else self.stream.read_u64()
                    else:
                        base_offset = 0
                    extent_count = self.stream.read_u16()
                    for j in range(extent_count):
                        if (atom_version == 1 or atom_version == 2) and index_size > 0:
                            extent_index = self.stream.read_u32() if index_size == 4 else self.stream.read_u64()
                        else:
                            extent_index = 0
                        extent_offset = self.stream.read_u32() if offset_size == 4 else self.stream.read_u64()
                        extent_length = self.stream.read_u32() if length_size == 4 else self.stream.read_u64()

                    # If this is the Exif item then decode it
                    if item_id == self.exif_id:
                        self.stream.push_position(extent_offset)
                        # Read Exif marker
                        marker_length = self.stream.read_u32()
                        marker = self.stream.read_string(4)
                        if marker != 'Exif':
                            sys.exit('Invalid exif marker')
                        self.stream.set_position(marker_length-4, io.SEEK_CUR)
                        # Parse Exif to extract creation date
                        t = TIFF()
                        t.init(self.stream)
                        t.parse()
                        self.image_time = t.get_image_time()
                        self.stream.pop_position()

            # All other types are skipped
            else:
                self.stream.set_position(atom_size - 8, io.SEEK_CUR)

    def get_image_time(self):
        return self.image_time


###############################################################################################################
# PNG format
###############################################################################################################

# For PNG format see https://www.w3.org/TR/PNG/

class PNG:
    def __init__(self):
        self.file_path = None
        self.image_time = None

    def load(self, file_path):
        self.file_path = file_path
        stream = FileStream(file_path, "rb")
        stream.set_endian(stream.BIG_ENDIAN)
        id1 = stream.read_u32()
        id2 = stream.read_u32()
        if id1 == 0x89504e47 and id2 == 0x0d0a1a0a:
            while not stream.is_eof():
                length = stream.read_u32()
                type = stream.read_string(4)
                if type == "tIME":
                    year = stream.read_u16()
                    month = stream.read_u8()
                    day = stream.read_u8()
                    hour = stream.read_u8()
                    minute = stream.read_u8()
                    second = stream.read_u8()
                    self.image_time = datetime.datetime(year, month, day, hour, minute, second)
                    crc = stream.read_u32()
                elif type == "tEXt":                # text
                    stream.set_position(length, io.SEEK_CUR)
                    crc = stream.read_u32()
                elif type == "zTXt":                # deflated text
                    stream.set_position(length, io.SEEK_CUR)
                    crc = stream.read_u32()
                elif type == "iTXt":                # international text
                    index = stream.get_position()
                    keyword = stream.read_nt_string()
                    compression_flag = stream.read_u8()
                    compression_method = stream.read_u8()
                    language_tag = stream.read_nt_string()
                    translated_keyword = stream.read_nt_string()
                    text_length = length - (stream.get_position() - index)
                    text = stream.read_string(text_length)
                    if keyword == "XML:com.adobe.xmp":
                        xml_root = ET.fromstring(text)
                        date_element = xml_root.find(".//{http://ns.adobe.com/photoshop/1.0/}DateCreated")
                        if date_element is not None:
                            try:
                                self.image_time = datetime.datetime.strptime(date_element.text, "%Y-%m-%dT%H:%M:%S")
                            except ValueError:
                                pass
                    crc = stream.read_u32()
                elif type == "IEND":
                    break
                else:
                    stream.set_position(length, io.SEEK_CUR)
                    crc = stream.read_u32()

    def get_image_time(self):
        return self.image_time


###############################################################################################################
# TIFF format
###############################################################################################################

# For TIFF format see http://www.fileformat.info/format/tiff/egff.htm
# For EXIF tags see http://www.sno.phy.queensu.ca/~phil/exiftool/TagNames/EXIF.html

class TIFF:
    def __init__(self):
        self.url = None
        self.stream = None
        self.ifd_start = 0
        self.image_time = None

    def init(self, stream):
        self.stream = stream

    def open(self, url):
        self.url = url
        self.stream = FileStream(url, "rb")

    def parse(self):
        self.parse_header()
        next_ifd = self.parse_ifd()
        while next_ifd != 0:
            self.stream.set_position(self.ifd_start + next_ifd)
            next_ifd = self.parse_ifd()

    def parse_header(self):
        # All IFD offsets are relative to this position
        self.ifd_start = self.stream.get_position()

        # Determine the file byte order
        byte_order = self.stream.read_u16()
        if byte_order == 0x4949:
            self.stream.set_endian(self.stream.LITTLE_ENDIAN)
        elif byte_order == 0x4d4d:
            self.stream.set_endian(self.stream.BIG_ENDIAN)
        else:
            raise ValueError

        # Check signature value
        fortytwo = self.stream.read_u16()
        if fortytwo != 42:
            raise ValueError

        # Now we get the offset to the first IFD
        ifd_offset = self.stream.read_u32()
        self.stream.set_position(self.ifd_start + ifd_offset)

    def parse_ifd(self):
        num_entries = self.stream.read_u16()
        for i in range(num_entries):
            tag = self.stream.read_u16()
            type = self.stream.read_u16()
            count = self.stream.read_u32()
            offset = self.ifd_start + self.stream.read_u32()

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
                        self.image_time = datetime.datetime.strptime(time_string, "%Y:%m:%d %H:%M:%S")
                    except ValueError:
                        # Sometimes dates can be malformed, e.g. Feb 29 in a non-leap year. Attempt to handle this.
                        try:
                            dt = datetime.datetime.strptime(time_string[0:7], "%Y:%m")
                            days = int(time_string[8:10])
                            delta = datetime.timedelta(days-1)
                            self.image_time = dt + delta
                        except ValueError:
                            pass
            else:
                pass

        next_ifd = self.stream.read_u32()
        return next_ifd

    def get_image_time(self):
        return self.image_time


###############################################################################################################
# Entry Point
###############################################################################################################

if __name__ == '__main__':

    if len(sys.argv) < 2:
        sys.exit(PURPOSE)

    # The first path is the collection into which files from all other paths are merged
    collection_path = os.path.expanduser(sys.argv[1]).replace('\\', '/')
    all_paths = sys.argv[1:]        # includes collection path
    collection_checksums = {}

    # Merge files from all paths into collection
    for next_path in all_paths:
        image_files = []
        next_path = os.path.expanduser(next_path).replace('\\', '/')
        if not os.path.exists(next_path):
            sys.exit('ERROR: {0} is not a valid path'.format(next_path))
        for path, folders, files in os.walk(next_path):
            for file_name in files:
                image_path = os.path.join(path, file_name).replace('\\', '/')
                base_name, ext = os.path.splitext(file_name)
                ext = ext.lower()

                # If the creation date is in the file name then this is considered the authoritative date
                image_time = None
                formats = [('%Y-%m-%d_%H%M%S', 17), ('%Y-%m-%d', 10), ('%Y-%m', 7), ('IMG_%Y%m%d_%H%M%S', 19), ('IMG-%Y%m%d', 12), ('VID_%Y%m%d', 12)]
                for format, length in formats:
                    try:
                        image_time = datetime.datetime.strptime(file_name[:length], format)
                        break
                    except ValueError:
                        pass

                # Attempt to parse the creation date from the image file metadata
                if not image_time:
                    if ext in ['.jpg', '.jpeg']:
                        image = JPEG()
                        image.load(image_path)
                        image_time = image.get_image_time()
                    elif ext in ['.mp4', '.m4v', '.mov', '.heic']:
                        image = MP4()
                        image.load(image_path)
                        image_time = image.get_image_time()
                    elif ext == '.png':
                        image = PNG()
                        image.load(image_path)
                        image_time = image.get_image_time()
                    elif ext in ['.tif', '.tiff']:
                        image = TIFF()
                        image.open(image_path)
                        image.parse()
                        image_time = image.get_image_time()
                    elif ext in ['.avi', '.mpg', '.mpeg']:
                        image = AVI()
                        image.load(image_path)
                        image_time = image.get_image_time()
                    elif ext in ['.bmp']:           # These image files don't contain an embedded creation date
                        pass
                    else:
                        # Not a supported image type so skip file
                        continue

                # Last resort; use file modification time as the image time
                if not image_time:
                    image_time = datetime.datetime.fromtimestamp(os.path.getmtime(image_path))

                # add to list of located image files
                image_files.append((image_path, image_time))

        # Move image files
        image_files.sort(key=lambda x: x[1])       # sort on timestamp
        for image_path, image_time in image_files:

            # Checksum file; skip if already in collection
            with open(image_path, 'rb') as f:
                file_checksum = hashlib.sha256(f.read()).digest()
            if file_checksum in collection_checksums:
                continue
            collection_checksums[file_checksum] = image_path

            # Create destination path
            file_folder, file_name = os.path.split(image_path)
            base_name, ext = os.path.splitext(file_name)
            ext = ext.lower()
            if ext == '.jpeg': ext = '.jpg'
            if ext == '.tiff': ext = '.tif'
            checksum_hex = file_checksum[0:10].hex()
            dst_name = '{0}-{1:02}-{2:02}_{3:02}{4:02}{5:02}_{6}'.format(image_time.year, image_time.month, image_time.day, image_time.hour, image_time.minute, image_time.second, checksum_hex)
            dst_path = os.path.join(collection_path, str(image_time.year), '{0}-{1:02}'.format(image_time.year, image_time.month))
            dst = os.path.join(dst_path, dst_name + ext).replace('\\', '/')

            # If file is already in correct place then skip
            if image_path == dst:
                continue

            # Create the parent folders for the file
            folder_name = os.path.split(dst)[0]
            if not os.path.exists(folder_name):
                try:
                    os.makedirs(folder_name)
                except os.error:
                    print('ERROR: Unable to create ' + folder_name)
                    continue

            # Move the file
            print('Moving {0} --> {1}'.format(image_path, dst))
            try:
                shutil.move(image_path, dst)
            except PermissionError:
                print('Failed to move {0}'.format(image_path))

    # Remove empty folders from collection
    empty_folders = []
    for path, folders, files in os.walk(collection_path):
        if len(folders) + len(files) == 0:
            empty_folders.append(path)
    for folder in empty_folders:
        print('Removing empty folder ' + folder)
        os.rmdir(folder)
