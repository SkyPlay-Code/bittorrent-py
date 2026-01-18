import os
import logging

class FileManager:
    """
    Manages reading and writing data across multiple files.
    Implements a Write-Back Cache to optimize Disk I/O.
    """
    def __init__(self, torrent):
        self.torrent = torrent
        self._open_files()
        
        # Caching
        self.write_cache = {} # Map: global_offset -> bytes
        self.cache_size = 0
        self.CACHE_THRESHOLD = 64 * 1024 * 1024 # 64MB Cache

    def _open_files(self):
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
        self.flush() # CRITICAL: Write remaining data to disk
        for fh in self.file_handles:
            fh['obj'].close()

    def write(self, global_offset: int, data: bytes):
        """
        Add data to cache. Flush if threshold reached.
        """
        self.write_cache[global_offset] = data
        self.cache_size += len(data)
        
        if self.cache_size >= self.CACHE_THRESHOLD:
            logging.info(f"Cache full ({self.cache_size/1024/1024:.2f}MB). Flushing to disk...")
            self.flush()

    def flush(self):
        """
        Sorts cached writes by offset and writes them sequentially to disk.
        """
        if not self.write_cache: return
        
        # Sort by offset to ensure sequential disk writes (Performance +++)
        sorted_offsets = sorted(self.write_cache.keys())
        
        for offset in sorted_offsets:
            data = self.write_cache[offset]
            self._write_to_disk(offset, data)
        
        self.write_cache.clear()
        self.cache_size = 0
        logging.info("Disk flush complete.")

    def _write_to_disk(self, global_offset, data):
        """
        Internal method to physically write bytes to file handles.
        """
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
            # f.flush() # Removed explicit flush per-write for performance. OS handles it.

            current_global_pos += amount_for_file
            data_cursor += amount_for_file
            bytes_to_write -= amount_for_file

            if bytes_to_write <= 0:
                break

    def read(self, global_offset: int, length: int) -> bytes:
        """
        Reads data. Checks RAM Cache first, then Disk.
        """
        # 1. Check Cache (RAM Speed)
        # Iterate cache to see if the request falls within a cached piece.
        # Since cache holds ~50-100 items max (pieces), this loop is negligible cost.
        for off, data in self.write_cache.items():
            if off <= global_offset < off + len(data):
                # We found the piece in RAM!
                start_in_piece = global_offset - off
                # Check if the request goes beyond this piece (unlikely for blocks)
                if start_in_piece + length <= len(data):
                    return data[start_in_piece : start_in_piece + length]

        # 2. Check Disk (IO Speed)
        return self._read_from_disk(global_offset, length)

    def _read_from_disk(self, global_offset, length):
        response_data = bytearray()
        bytes_to_read = length
        current_global_pos = global_offset

        for fh in self.file_handles:
            tf = fh['info']
            f = fh['obj']

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