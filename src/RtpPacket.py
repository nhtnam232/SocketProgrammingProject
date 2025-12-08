
import sys
from time import time

HEADER_SIZE = 12

class RtpPacket:    
    def __init__(self):
        self.header = bytearray(HEADER_SIZE)
        self.payload = b""

    def encode(self, version, padding, extension, cc, seqnum, marker, pt, ssrc, payload):
        """Encode the RTP packet with header fields and payload."""
        # Timestamp: v� d? s? d?ng epoch seconds (demo). Trong th?c t? n�n d�ng clock media.
        timestamp = int(time())

        # T?o header 12 byte
        header = bytearray(HEADER_SIZE)

        # Byte 0: V(2) | P(1) | X(1) | CC(4)
        header[0] = ((version & 0x03) << 6) \
                    | ((padding & 0x01) << 5) \
                    | ((extension & 0x01) << 4) \
                    | (cc & 0x0F)

        # Byte 1: M(1) | PT(7)
        header[1] = ((marker & 0x01) << 7) \
                    | (pt & 0x7F)
        # Byte 2-3: Sequence Number (16-bit big-endian)
        header[2] = (seqnum >> 8) & 0xFF
        header[3] = seqnum & 0xFF

        # Byte 4-7: Timestamp (32-bit big-endian)
        header[4] = (timestamp >> 24) & 0xFF
        header[5] = (timestamp >> 16) & 0xFF
        header[6] = (timestamp >> 8) & 0xFF
        header[7] = timestamp & 0xFF

        # Byte 8-11: SSRC (32-bit big-endian)
        header[8]  = (ssrc >> 24) & 0xFF
        header[9]  = (ssrc >> 16) & 0xFF
        header[10] = (ssrc >> 8) & 0xFF
        header[11] = ssrc & 0xFF

        # G�n header v� payload v�o ??i t??ng
        self.header = header
        self.payload = payload if isinstance(payload, (bytes, bytearray)) else bytes(payload)

    def decode(self, byteStream):
        """Decode the RTP packet."""
        self.header = bytearray(byteStream[:HEADER_SIZE])
        self.payload = byteStream[HEADER_SIZE:]
    
    def version(self):
        """Return RTP version."""
        return int(self.header[0] >> 6)
    
    def seqNum(self):
        """Return sequence (frame) number."""
        seqNum = (self.header[2] << 8) | self.header[3]
        return int(seqNum)
    
    def timestamp(self):
        """Return timestamp."""
        timestamp = (self.header[4] << 24) | (self.header[5] << 16) | (self.header[6] << 8) | self.header[7]
        return int(timestamp)
    
    def payloadType(self):
        """Return payload type."""
        pt = self.header[1] & 0x7F
        return int(pt)
    
    def getPayload(self):
        """Return payload."""
        return self.payload
        
    def getPacket(self):
        """Return RTP packet (header + payload)."""
        return self.header + self.payload
    def getMarker(self):
        return (self.header[1] >> 7) & 1
