import unittest
import torch
from spinor_rnn import SpinorRNN, rotation, corpus, bits, train


class SpinorTests(unittest.TestCase):
    def setUp(self):
        torch.set_num_threads(1)
        torch.manual_seed(42)

    def test_su2_and_noncommutativity(self):
        angles = torch.tensor([[0.4, 0., 0.], [0., 0., 0.7]])
        a, b = rotation(angles)
        identity = torch.eye(2, dtype=torch.complex64)
        self.assertTrue(torch.allclose(a.mH @ a, identity, atol=1e-6))
        self.assertTrue(torch.allclose(torch.linalg.det(a), torch.tensor(1+0j), atol=1e-6))
        self.assertGreater(float(torch.linalg.vector_norm(a @ b - b @ a)), 0.1)

    def test_gradients_and_causality(self):
        model = SpinorRNN(2)
        data = corpus(5, 2, 8)
        original = model(data)
        changed = data.clone()
        changed[:, 5:] = 0
        self.assertTrue(torch.equal(original[:, :5], model(changed)[:, :5]))
        original.square().mean().backward()
        self.assertTrue(torch.isfinite(model.angles.weight.grad).all())
        self.assertGreater(float(model.angles.weight.grad.abs().sum()), 0)

    def test_learning_on_unseen_records(self):
        model = SpinorRNN(4)
        training, validation, test = corpus(11, 32), corpus(22, 16), corpus(33, 16)
        initial = bits(model, test)
        train(model, training, validation, 12)
        self.assertLess(bits(model, test), initial - 0.5)


if __name__ == '__main__':
    unittest.main()
