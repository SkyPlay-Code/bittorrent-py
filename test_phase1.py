import unittest
from collections import OrderedDict
from bencoding import Decoder, Encoder

class TestBencoding(unittest.TestCase):
    def test_decode_integer(self):
        res = Decoder(b'i123e').decode()
        self.assertEqual(res, 123)

    def test_decode_string(self):
        res = Decoder(b'12:Middle Earth').decode()
        self.assertEqual(res, b'Middle Earth')

    def test_decode_list(self):
        res = Decoder(b'l4:spam4:eggsi123ee').decode()
        self.assertEqual(res, [b'spam', b'eggs', 123])

    def test_decode_dict(self):
        # Note: PDF Example d3:cow3:moo4:spam4:eggse
        res = Decoder(b'd3:cow3:moo4:spam4:eggse').decode()
        self.assertIsInstance(res, OrderedDict)
        self.assertEqual(res[b'cow'], b'moo')
        self.assertEqual(res[b'spam'], b'eggs')

    def test_encode_complex(self):
        # Reproduce the complex example from PDF Page 2
        data = [b'spam', b'eggs', 123]
        encoded = Encoder(data).encode()
        self.assertEqual(encoded, b'l4:spam4:eggsi123ee')

    def test_encode_dict(self):
        data = OrderedDict()
        data[b'cow'] = b'moo'
        data[b'spam'] = b'eggs'
        encoded = Encoder(data).encode()
        self.assertEqual(encoded, b'd3:cow3:moo4:spam4:eggse')

if __name__ == '__main__':
    unittest.main()