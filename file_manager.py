import os
import logging
import asyncio
from concurrent.futures import ThreadPoolExecutor

class FileManager:
    """
    Manages reading and writing data across multiple files.
    Features:
    - ThreadPool execution for Non-Blocking I/O
    - Sparse File creation (instant allocation)
    - Write-Back Caching
    """
    def __init__(self, torrent):
        self.torrent = torrent
        # Create a pool for Disk I/O
        self.io_executor = ThreadPoolExecutor(max_workers=1) 
        self.write_cache = {} 
        self.cache_size = 0
        self.CACHE_THRESHOLD = 64 * 1024 * 1024 
        
        # We perform file opening synchronously on init to fail fast, 
        # but file creation is now sparse (fast).
        self._open_files()

    def _open_files(self):
        self.file_handles = []
        for tf in self.torrent.files:
            directory = os.path.dirname(tf.path)
            if directory and not os.path.exists(directory):
                os.makedirs(directory)
            
            # Sparse File Creation
            if not os.path.exists(tf.path):
                with open(tf.path, 'wb') as f:
                    # Resize without writing bytes (Sparse)
                    f.truncate(tf.length)
            
            # Open for Read/Write
            f = open(tf.path, 'rb+')
            self.file_handles.append({
                'obj': f,
                'info': tf
            })

    def close(self):
        # Flush is async, but close is usually called at shutdown.
        # We enforce a sync flush here.
        if self.write_cache:
            self._flush_sync()
        for fh in self.file_handles:
            fh['obj'].close()
        self.io_executor.shutdown()

    async def write(self, global_offset: int, data: bytes):
        """
        Non-blocking write. Adds to cache. If cache full, offloads flush to thread.
        """
        self.write_cache[global_offset] = data
        self.cache_size += len(data)
        
        if self.cache_size >= self.CACHE_THRESHOLD:
            logging.info(f"Cache full ({self.cache_size/1024/1024:.2f}MB). Flushing...")
            # We schedule the flush but don't wait for it to finish immediately
            # to keep accepting network data.
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(self.io_executor, self._flush_sync)

    def _flush_sync(self):
        """
        Blocking flush (run in thread).
        """
        if not self.write_cache: return
        
        sorted_offsets = sorted(self.write_cache.keys())
        for offset in sorted_offsets:
            data = self.write_cache[offset]
            self._write_to_disk_sync(offset, data)
        
        self.write_cache.clear()
        self.cache_size = 0

    def _write_to_disk_sync(self, global_offset, data):
        bytes_to_write = len(data)
        current_global_pos = global_offset
        data_cursor = 0

        for fh in self.file_handles:
            tf = fh['info']
            f = fh['obj']

            if tf.end_offset <= current_global_pos: continue
            if tf.start_offset >= current_global_pos + bytes_to_write: break 

            file_write_start = max(0, current_global_pos - tf.start_offset)
            file_remaining_cap = tf.length - file_write_start
            amount_for_file = min(bytes_to_write, file_remaining_cap)

            chunk = data[data_cursor : data_cursor + amount_for_file]

            f.seek(file_write_start)
            f.write(chunk)
            
            current_global_pos += amount_for_file
            data_cursor += amount_for_file
            bytes_to_write -= amount_for_file

            if bytes_to_write <= 0: break

    async def read(self, global_offset: int, length: int) -> bytes:
        """
        Async read. Checks Cache first (fast), then Disk (thread).
        """
        # 1. Cache Hit?
        for off, data in self.write_cache.items():
            if off <= global_offset < off + len(data):
                start = global_offset - off
                if start + length <= len(data):
                    return data[start : start + length]

        # 2. Disk Read (Offload to thread)
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self.io_executor, self._read_sync, global_offset, length)

    def _read_sync(self, global_offset, length):
        response_data = bytearray()
        bytes_to_read = length
        current_global_pos = global_offset

        for fh in self.file_handles:
            tf = fh['info']
            f = fh['obj']

            if tf.end_offset <= current_global_pos: continue
            if tf.start_offset >= current_global_pos + bytes_to_read: break

            file_read_start = max(0, current_global_pos - tf.start_offset)
            file_remaining_cap = tf.length - file_read_start
            amount_for_file = min(bytes_to_read, file_remaining_cap)

            f.seek(file_read_start)
            chunk = f.read(amount_for_file)
            response_data.extend(chunk)

            current_global_pos += amount_for_file
            bytes_to_read -= amount_for_file

            if bytes_to_read <= 0: break
        
        return bytes(response_data)