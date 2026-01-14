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
            # Ensure directory exists
            directory = os.path.dirname(tf.path)
            if directory and not os.path.exists(directory):
                os.makedirs(directory)
            
            # Open file (create if missing)
            # 'r+b' requires file to exist, so we use 'a+b' to create then 'r+b' to manage
            if not os.path.exists(tf.path):
                open(tf.path, 'wb').close() # Create empty file
                
            f = open(tf.path, 'rb+')
            
            # Pre-allocate space (optional, but prevents fragmentation)
            # Check size, if 0, extend.
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
        """
        Writes data to the correct location(s) based on the global offset.
        Handles cases where data spans across file boundaries.
        """
        bytes_to_write = len(data)
        current_global_pos = global_offset
        data_cursor = 0

        for fh in self.file_handles:
            tf = fh['info']
            f = fh['obj']

            # Check if this file overlaps with the data range
            # Range of this file: [tf.start_offset, tf.end_offset)
            # Range of data: [current_global_pos, current_global_pos + bytes_to_write)

            if tf.end_offset <= current_global_pos:
                # This file is entirely before the data
                continue
            
            if tf.start_offset >= current_global_pos + bytes_to_write:
                # This file is entirely after the data
                break 

            # Calculate overlap
            file_write_start = max(0, current_global_pos - tf.start_offset)
            
            # How much can we write to this file?
            # It's bounded by the file's remaining capacity and the data's remaining length
            file_remaining_cap = tf.length - file_write_start
            amount_for_file = min(bytes_to_write, file_remaining_cap)

            # Get the chunk of data
            chunk = data[data_cursor : data_cursor + amount_for_file]

            # Write
            f.seek(file_write_start)
            f.write(chunk)
            f.flush()

            # Update cursors
            current_global_pos += amount_for_file
            data_cursor += amount_for_file
            bytes_to_write -= amount_for_file

            if bytes_to_write <= 0:
                break