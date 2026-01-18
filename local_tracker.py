import http.server
import socketserver
import urllib.parse
import struct
import socket
import sys

# Store peers: { info_hash_bytes: [ (ip, port), ... ] }
SWARM = {}

class TrackerHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        
        if parsed.path == "/announce":
            try:
                params = urllib.parse.parse_qs(parsed.query)
                
                # Extract Info Hash (raw bytes)
                # urllib parses query into strings, we need to be careful with binary data in URLs
                # Ideally we parse the raw query string manually for binary info_hash
                # But mostly clients URL-encode it.
                
                # Robust info_hash extraction from raw query string
                query_string = parsed.query
                # This is tricky because standard libs assume utf-8. 
                # Let's rely on 'port' and 'compact' being present.
                
                if 'info_hash' not in params:
                    self.send_error(400, "Missing info_hash")
                    return

                # For this simple tracker, we accept the info_hash as the string representation from library
                # OR we act blindly.
                
                # Let's clean the params
                info_hash = params['info_hash'][0].encode('latin-1') # Re-encode to bytes
                port = int(params['port'][0])
                peer_ip = self.client_address[0]
                
                # Register Peer
                if info_hash not in SWARM:
                    SWARM[info_hash] = []
                
                # Add if not exists
                peer_entry = (peer_ip, port)
                if peer_entry not in SWARM[info_hash]:
                    SWARM[info_hash].append(peer_entry)
                    print(f"Registered peer {peer_ip}:{port}")

                # Build Peer List (Compact Format)
                peers_binary = b""
                for p_ip, p_port in SWARM[info_hash]:
                    # Don't return self? (Optional, clients handle it)
                    # if p_ip == peer_ip and p_port == port: continue
                    try:
                        peers_binary += socket.inet_aton(p_ip) + struct.pack(">H", p_port)
                    except: pass

                # Bencode Response: d8:intervali1800e5:peers...e
                response = b"d8:intervali1800e5:peers" + str(len(peers_binary)).encode() + b":" + peers_binary + b"e"
                
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(response)
                
            except Exception as e:
                print(f"Tracker Error: {e}")
                self.send_error(500)
        else:
            self.send_error(404)

    def log_message(self, format, *args):
        return # Silence standard logs

if __name__ == "__main__":
    PORT = 8000
    print(f"Starting Local Tracker on Port {PORT}...")
    with socketserver.TCPServer(("0.0.0.0", PORT), TrackerHandler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass