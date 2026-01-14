import asyncio
import sys
import logging
from client import TorrentClient

def main():
    if len(sys.argv) < 2:
        print("Usage: python main.py <torrent_file>")
        sys.exit(1)

    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    
    # Suppress noisy logs
    logging.getLogger('asyncio').setLevel(logging.WARNING)
    
    client = TorrentClient(sys.argv[1])
    
    try:
        asyncio.run(client.start())
    except KeyboardInterrupt:
        print("\nExiting...")

if __name__ == '__main__':
    main()