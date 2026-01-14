import logging
from collections import OrderedDict

# Constants for Bencoding tokens
TOKEN_INTEGER = b'i'
TOKEN_LIST = b'l'
TOKEN_DICT = b'd'
TOKEN_END = b'e'
TOKEN_STRING_SEPARATOR = b':'

class Decoder:
    """
    Decodes bencoded binary data into Python objects.
    Reference: Page 2 of PDF.
    """
    def __init__(self, data: bytes):
        if not isinstance(data, bytes):
            raise TypeError('Argument "data" must be of type bytes')
        self._data = data
        self._index = 0

    def decode(self):
        """
        Decodes the bencoded data and returns the matching Python object.
        """
        c = self._peek()
        if c is None:
            raise EOFError('Unexpected end of file')
        elif c == TOKEN_INTEGER:
            self._consume()  # eat 'i'
            return self._decode_int()
        elif c == TOKEN_LIST:
            self._consume()  # eat 'l'
            return self._decode_list()
        elif c == TOKEN_DICT:
            self._consume()  # eat 'd'
            return self._decode_dict()
        elif c in b'0123456789':
            return self._decode_string()
        elif c == TOKEN_END:
            return None
        else:
            raise RuntimeError('Invalid token at index {}: {}'.format(self._index, c))

    def _peek(self):
        if self._index + 1 >= len(self._data):
            return None
        return self._data[self._index:self._index+1]

    def _consume(self):
        self._index += 1

    def _decode_int(self):
        end = self._data.find(TOKEN_END, self._index)
        if end == -1:
            raise RuntimeError('Invalid integer: missing "e" end token')
        
        number_string = self._data[self._index:end]
        self._index = end + 1 # move past 'e'
        return int(number_string)

    def _decode_string(self):
        colon = self._data.find(TOKEN_STRING_SEPARATOR, self._index)
        if colon == -1:
            raise RuntimeError('Invalid string: missing ":" separator')
        
        length_string = self._data[self._index:colon]
        if not length_string.isdigit():
             raise RuntimeError('Invalid string length')
             
        length = int(length_string)
        self._index = colon + 1 # move past ':'
        
        string_data = self._data[self._index : self._index + length]
        self._index += length
        return string_data

    def _decode_list(self):
        res = []
        # Recursive decode until we hit 'e'
        while self._data[self._index:self._index+1] != TOKEN_END:
            res.append(self.decode())
        self._consume() # eat 'e'
        return res

    def _decode_dict(self):
        res = OrderedDict()
        while self._data[self._index:self._index+1] != TOKEN_END:
            key = self.decode()
            obj = self.decode()
            res[key] = obj
        self._consume() # eat 'e'
        return res

class Encoder:
    """
    Encodes Python objects into bencoded binary data.
    """
    def __init__(self, data):
        self._data = data

    def encode(self) -> bytes:
        return self._encode_next(self._data)

    def _encode_next(self, data):
        if isinstance(data, int):
            return self._encode_int(data)
        elif isinstance(data, str):
            return self._encode_string(data.encode('utf-8'))
        elif isinstance(data, bytes):
            return self._encode_string(data)
        elif isinstance(data, list):
            return self._encode_list(data)
        elif isinstance(data, (dict, OrderedDict)):
            return self._encode_dict(data)
        else:
            # Fallback for unexpected types, treat as string or error
            raise TypeError('Cannot encode type: {}'.format(type(data)))

    def _encode_int(self, value):
        return str(value).encode('utf-8').join([TOKEN_INTEGER, TOKEN_END])

    def _encode_string(self, value: bytes):
        length = str(len(value)).encode('utf-8')
        return length + TOKEN_STRING_SEPARATOR + value

    def _encode_list(self, data):
        encoded = b''.join([self._encode_next(item) for item in data])
        return TOKEN_LIST + encoded + TOKEN_END

    def _encode_dict(self, data):
        # Dictionary keys must be bencoded strings and sorted lexicographically
        # If it's an OrderedDict, we assume order is preserved, but standard spec 
        # requires sorted keys. We will sort standard dicts.
        encoded_items = []
        
        # Ensure we are working with sorted keys if it's not an OrderedDict
        # Note: In strict Bencoding, keys must be strings/bytes.
        keys = list(data.keys())
        if not isinstance(data, OrderedDict):
            keys.sort()
            
        for key in keys:
            encoded_key = self._encode_next(key)
            encoded_val = self._encode_next(data[key])
            encoded_items.append(encoded_key + encoded_val)
            
        return TOKEN_DICT + b''.join(encoded_items) + TOKEN_END