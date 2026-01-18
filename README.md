### Final Architecture Review

**1. Core Protocol & Networking**
*   **`main.py`**: Entry point and logging configuration.
*   **`client.py`**: The central orchestrator handling the event loop, worker scaling, and UI dashboard.
*   **`peer.py`**: A robust, "immortal" worker implementing the BitTorrent Wire Protocol, Extensions, and PEX.
*   **`message.py`**: Binary serialization for protocol messages (Handshake, Bitfield, Extended, etc.).

**2. Data & Storage**
*   **`torrent.py`**: Parsers for `.torrent` files and Magnet URIs.
*   **`piece_manager.py`**: The brain ensuring data integrity, "Rarest First" selection, and Resume capability.
*   **`file_manager.py`**: Handles multi-file reads/writes with a RAM Write-Back Cache for performance.
*   **`bencoding.py`**: Custom encoder/decoder for the BTorrent serialization format.

**3. Discovery & Connectivity**
*   **`tracker.py`**: Hybrid HTTP/UDP tracker client.
*   **`dht.py` / `kademlia.py`**: Distributed Hash Table for trackerless peer discovery.
*   **`metadata.py`**: Handles the download of `.torrent` files from peers via Magnet links.
*   **`nat.py`**: UPnP implementation for automatic port forwarding.
*   **`utp.py`**: Micro Transport Protocol (UDP) framework.

**4. Security & Optimization**
*   **`connection_manager.py`**: Tit-for-Tat Choking algorithm to optimize swarm health.
*   **`crypto_utils.py` / `mse.py`**: RC4 encryption and Diffie-Hellman key exchange to bypass ISP throttling.

### How to Run

The client is ready for daily use.

**Download via Magnet:**
```bash
python main.py "magnet:?xt=urn:btih:..."
```

**Download via Torrent File:**
```bash
python main.py filename.torrent
```

**Features active by default:**
*   **Dashboard:** Real-time speed, ETA, and peer counts.
*   **Persistence:** `Ctrl+C` to stop. Run again to Resume.
*   **Encryption:** Automatically attempts MSE handshake.
*   **Discovery:** Uses Trackers, DHT, PEX, and LSD (Local Service Discovery via Multicast, implicitly supported by PEX if local peers are found).