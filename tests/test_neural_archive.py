import unittest
import torch
from neural_archive import encode, decode
from spinor_rnn import SpinorRNN, ConventionalRNN


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        torch.set_num_threads(1)
        torch.manual_seed(3)

    def test_exact_roundtrips(self):
        for model in (SpinorRNN(2), ConventionalRNN()):
            for data in (b'', b'a', b'abcabc'*10, bytes(range(256)), bytes([0])*64):
                self.assertEqual(decode(encode(data, model), model), data)

    def test_reject_damage_and_wrong_weights(self):
        model = SpinorRNN(2)
        archive = encode(b'hello world'*3, model)
        for damaged in (archive[:-1], archive+b'\0', archive[:10],
                        archive[:-1]+bytes([archive[-1]^128])):
            with self.assertRaises(ValueError):
                decode(damaged, model)
        with self.assertRaises(ValueError):
            decode(archive, SpinorRNN(2))


if __name__ == '__main__':
    unittest.main()
