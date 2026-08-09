import os
from .constants import SMASHREMIX_PATH as smashremix_path
from .hex_util import find_last_hex_id
from .logger import logger
from dataclasses import dataclass

@dataclass
class ImportFile:
    id: int
    name: str
    path: str
    internal_file_table_offset: int
    internal_file_resource_offset: int
    reqlist_path: str = None
    compression_level: int = 0
    extend_reqlist: int = 0

    def __post_init__(self):
        if self.name is None:
            self.name = os.path.basename(self.path).upper()
        else:
            self.name = self.name.upper()

        self.name = self.name.replace("/", "_")

        self.validate_reqlist()

    def to_csv_entry(self, relative_path: str = ""):
        return (
            f","
            f"{relative_path}{self.path},"
            f"{1 if self.compression_level != 0 else 0},"
            f"{self.internal_file_table_offset:04X},"
            f"{self.internal_file_resource_offset:04X},"
            f"{relative_path+self.reqlist_path if self.reqlist_path else ''},"
            f"{self.compression_level}"
        )

    def validate_reqlist(self):
        '''
            Validate if file reqlist's size matches provided reqlist txt
        '''
        with open(self.path, 'rb') as _f:
            data = bytearray(_f.read())

        # Process second linked list to get the number of entries
        current_address = self.internal_file_resource_offset
        file_list_size = 0

        logger.debug(f"== Validating reqlist for file: {self.path} ==")

        while current_address != 0x3FFFC:
            file_list_size += 1
            next_list_item = int.from_bytes(
                data[current_address:current_address+2], byteorder='big')
            logger.debug(
                f">Entry #{file_list_size} at {current_address:04X} - Next {next_list_item:04X}"
            )
            current_address = int(next_list_item * 4)

        # Read reqlist and get the number of entries
        reqlist_list_size = 0

        try:
            with open(self.reqlist_path, 'r', encoding='utf-8') as _f:
                contents = _f.readlines()
                # Remove "END OF REQLIST" line if present
                contents = [
                    l for l in contents if not l.startswith("END OF")]
                reqlist_list_size = len(contents)
        except:
            # No reqlist?
            pass

        if file_list_size+self.extend_reqlist != reqlist_list_size:
            logger.error(
                f"File {self.path}: reqlist size mismatch: "
                f"File's linked list length: {file_list_size} (+{self.extend_reqlist} extend_reqlist), "
                f"reqlist txt length: {reqlist_list_size}."
            )
            exit(1)


class FileManager:
    _import_files: ImportFile = []
    _last_file_id = None

    @staticmethod
    def get_last_file_id():
        if FileManager._last_file_id is None:
            FileManager._last_file_id = int(find_last_hex_id(
                os.path.join(smashremix_path, 'src/File.asm')), 16)
            logger.info(f"Last file ID: 0x{FileManager._last_file_id:04X}")
        return FileManager._last_file_id

    @staticmethod
    def get_new_file_id():
        logger.info(
            f"Generating new file ID from last ID: 0x{FileManager._last_file_id:04X}")
        FileManager._last_file_id += 1
        return FileManager._last_file_id

    @staticmethod
    def add_file(
        path: str, name: str,
        internal_file_table_offset: int | str, internal_file_resource_offset: int | str,
        reqlist_path: str = None, compression_level: int = 0, extend_reqlist: int = 0
    ) -> ImportFile:
        file_id = FileManager.get_new_file_id()

        # Convert hex to int if necessary
        if isinstance(internal_file_table_offset, str):
            internal_file_table_offset = int(internal_file_table_offset, 16)
        if isinstance(internal_file_resource_offset, str):
            internal_file_resource_offset = int(
                internal_file_resource_offset, 16)

        import_file = ImportFile(
            id=file_id,
            name=name,
            path=path,
            internal_file_table_offset=internal_file_table_offset,
            internal_file_resource_offset=internal_file_resource_offset,
            reqlist_path=reqlist_path,
            compression_level=compression_level,
            extend_reqlist=extend_reqlist
        )
        FileManager._import_files.append(import_file)
        logger.info(f"Added file: {import_file}")
        return import_file

    @staticmethod
    def get_import_files() -> list[ImportFile]:
        return FileManager._import_files

    @staticmethod
    def gen_import_strings(relative_path: str = ""):
        import_strings = []
        for import_file in FileManager._import_files:
            import_strings.append(
                "ADD,"+import_file.to_csv_entry(relative_path=relative_path)
            )
        return import_strings


if os.path.exists(os.path.join(smashremix_path, "src/File.asm")):
    FileManager.get_last_file_id()
