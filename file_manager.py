import os

class FileManager:
    """
    Manages reading and writing data across multiple files.
    """
    def __init__(self, torrent):
        self.torrent = torrent
        self._open_files()

    def _open_files(self):
        """
        Creates necessary directories and opens file handles.
        """
        self.file_handles = []
        for tf in self.torrent.files:
            directory = os.path.dirname(tf.path)
            if directory and not os.path.exists(directory):
                os.makedirs(directory)
            
            if not os.path.exists(tf.path):
                open(tf.path, 'wb').close() 
                
            f = open(tf.path, 'rb+')
            f.seek(0, os.SEEK_END)
            if f.tell() != tf.length:
                f.truncate(tf.length)
                
            self.file_handles.append({
                'obj': f,
                'info': tf
            })

    def close(self):
        for fh in self.file_handles:
            fh['obj'].close()

    def write(self, global_offset: int, data: bytes):
        bytes_to_write = len(data)
        current_global_pos = global_offset
        data_cursor = 0

        for fh in self.file_handles:
            tf = fh['info']
            f = fh['obj']

            if tf.end_offset <= current_global_pos:
                continue
            
            if tf.start_offset >= current_global_pos + bytes_to_write:
                break 

            file_write_start = max(0, current_global_pos - tf.start_offset)
            file_remaining_cap = tf.length - file_write_start
            amount_for_file = min(bytes_to_write, file_remaining_cap)

            chunk = data[data_cursor : data_cursor + amount_for_file]

            f.seek(file_write_start)
            f.write(chunk)
            f.flush()

            current_global_pos += amount_for_file
            data_cursor += amount_for_file
            bytes_to_write -= amount_for_file

            if bytes_to_write <= 0:
                break

    def read(self, global_offset: int, length: int) -> bytes:
        """
        Reads data starting from global_offset.
        Handles reading across file boundaries.
        """
        response_data = bytearray()
        bytes_to_read = length
        current_global_pos = global_offset

        for fh in self.file_handles:
            tf = fh['info']
            f = fh['obj']

            # Check overlap
            if tf.end_offset <= current_global_pos:
                continue
            
            if tf.start_offset >= current_global_pos + bytes_to_read:
                break

            file_read_start = max(0, current_global_pos - tf.start_offset)
            file_remaining_cap = tf.length - file_read_start
            amount_for_file = min(bytes_to_read, file_remaining_cap)

            f.seek(file_read_start)
            chunk = f.read(amount_for_file)
            response_data.extend(chunk)

            current_global_pos += amount_for_file
            bytes_to_read -= amount_for_file

            if bytes_to_read <= 0:
                break
        
        return bytes(response_data)