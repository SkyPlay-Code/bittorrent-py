import unittest
# Import all test modules
from test_phase1 import TestBencoding
from test_phase2 import TestTorrentClass
from test_phase3 import TestTracker
from test_phase4 import TestPeerProtocol, TestPeerCommunication
from test_phase5 import TestPieceManager
from test_phase6 import TestClientIntegration

if __name__ == '__main__':
    unittest.main()