import asyncio
import socket
import logging
import struct
import aiohttp
import re
from urllib.parse import urlparse

# Constants for SSDP
SSDP_ADDR = "239.255.255.250"
SSDP_PORT = 1900
SSDP_MX = 2
SSDP_ST = "urn:schemas-upnp-org:service:WANIPConnection:1"

SSDP_REQUEST = (
    "M-SEARCH * HTTP/1.1\r\n"
    f"HOST: {SSDP_ADDR}:{SSDP_PORT}\r\n"
    'MAN: "ssdp:discover"\r\n'
    f"MX: {SSDP_MX}\r\n"
    f"ST: {SSDP_ST}\r\n"
    "\r\n"
).encode()

class NatTraverser:
    def __init__(self):
        self.router_url = None
        self.control_url = None
        self.service_type = None
        self.internal_ip = self._get_internal_ip()

    def _get_internal_ip(self):
        """
        Determines the local IP address of this machine.
        """
        try:
            # We don't actually connect, but this logic selects the interface 
            # used to reach the internet.
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "127.0.0.1"

    async def map_port(self, port, protocol="TCP", description="BitTorrent Client"):
        """
        Main entry point: Discover router -> Get Control URL -> Add Mapping.
        """
        logging.info(f"Attempting UPnP Port Mapping for {port} ({protocol})...")
        
        try:
            # 1. Discover
            if not self.control_url:
                await self._discover_gateway()
                
            if not self.control_url:
                logging.warning("UPnP: No Gateway found.")
                return False

            # 2. Map
            await self._send_add_port_mapping(port, protocol, description)
            logging.info(f"UPnP: Port {port} mapped successfully on {self.internal_ip}")
            return True
            
        except Exception as e:
            logging.error(f"UPnP Failed: {e}")
            return False

    async def _discover_gateway(self):
        """
        Sends SSDP multicast and parses response to find the router's XML location.
        """
        loop = asyncio.get_running_loop()
        
        # Create UDP socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(('0.0.0.0', 0)) # Bind to ephemeral port
        sock.setblocking(False)

        logging.info("UPnP: Sending SSDP discovery...")
        sock.sendto(SSDP_REQUEST, (SSDP_ADDR, SSDP_PORT))

        try:
            # Wait for response (simplified single response handling)
            # In a robust lib we would gather multiple and filter.
            data = await asyncio.wait_for(loop.sock_recv(sock, 4096), timeout=3.0)
            response = data.decode()
            
            # Parse 'LOCATION: http://...'
            location_match = re.search(r'LOCATION:\s*(http://[^\r\n]+)', response, re.IGNORECASE)
            if not location_match:
                raise ValueError("No LOCATION in SSDP response")
                
            self.router_url = location_match.group(1)
            logging.info(f"UPnP: Router found at {self.router_url}")
            
            # 3. Fetch XML and find Control URL
            await self._parse_router_xml()
            
        except asyncio.TimeoutError:
            logging.warning("UPnP: SSDP Discovery Timed out")
        finally:
            sock.close()

    async def _parse_router_xml(self):
        async with aiohttp.ClientSession() as session:
            async with session.get(self.router_url) as resp:
                if resp.status != 200:
                    raise ValueError(f"Failed to fetch router XML: {resp.status}")
                xml_content = await resp.text()

        # Simple Regex parsing to avoid heavy XML libraries
        # We look for WANIPConnection or WANPPPConnection
        # Then we find the <controlURL> inside that service block.
        
        # This is a naive parser. Real UPnP XML is nested. 
        # We search for the service type first.
        
        service_types = [
            "urn:schemas-upnp-org:service:WANIPConnection:1",
            "urn:schemas-upnp-org:service:WANPPPConnection:1"
        ]
        
        target_service = None
        for st in service_types:
            if st in xml_content:
                target_service = st
                break
        
        if not target_service:
            raise ValueError("No compatible WAN connection service found")
        
        self.service_type = target_service
        
        # Find the block for this service (Very simplified)
        # We assume controlURL is near the serviceType
        # A robust way: Split by serviceType, take the suffix, find first controlURL
        
        split_xml = xml_content.split(target_service)
        if len(split_xml) < 2:
            raise ValueError("XML parsing error")
            
        suffix = split_xml[1]
        control_match = re.search(r'<controlURL>(.*?)</controlURL>', suffix)
        if not control_match:
            raise ValueError("No controlURL found")
            
        control_path = control_match.group(1)
        
        # Construct full URL
        parsed = urlparse(self.router_url)
        self.control_url = f"{parsed.scheme}://{parsed.netloc}{control_path}"

    async def _send_add_port_mapping(self, port, protocol, description):
        soap_body = f"""<?xml version="1.0"?>
        <s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">
        <s:Body>
            <u:AddPortMapping xmlns:u="{self.service_type}">
                <NewRemoteHost></NewRemoteHost>
                <NewExternalPort>{port}</NewExternalPort>
                <NewProtocol>{protocol}</NewProtocol>
                <NewInternalPort>{port}</NewInternalPort>
                <NewInternalClient>{self.internal_ip}</NewInternalClient>
                <NewEnabled>1</NewEnabled>
                <NewPortMappingDescription>{description}</NewPortMappingDescription>
                <NewLeaseDuration>0</NewLeaseDuration>
            </u:AddPortMapping>
        </s:Body>
        </s:Envelope>"""

        headers = {
            'Content-Type': 'text/xml',
            'SOAPAction': f'"{self.service_type}#AddPortMapping"'
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(self.control_url, data=soap_body, headers=headers) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise ValueError(f"SOAP Error {resp.status}: {text}")