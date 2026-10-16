import random
import numpy as np

class EvolutionEngine:
    """
    Evolves compression functions inside CognitiveSingularityEngine.
    Selects the best-performing functions and propagates them to peers.
    """

    def __init__(self, engine):
        self.engine = engine
        self.population = []
        self.tracker = EvolutionTracker()

    # ---------------------------------------------------------
    # POPULATION GENERATION
    # ---------------------------------------------------------

    def _generate_population(self, size):
        """
        Create a population of mutated compression functions.
        """
        funcs = []

        base_func = self.engine.kindergarten.compression_func

        for _ in range(size):
            funcs.append(self._mutate_function(base_func))

        return funcs

    def _mutate_function(self, func):
        """
        Wrap the base compression function with slight random noise.
        """
        def mutated(psi):
            result = func(psi)
            if isinstance(result, dict):
                result["mutation_noise"] = float(np.random.randn() * 0.01)
            return result

        return mutated

    # ---------------------------------------------------------
    # SELECTION
    # ---------------------------------------------------------

    def _score(self, psi, func):
        """
        Score a compression function based on stability of QT state.
        """
        compressed = func(psi)
        qt_state = self.engine.qt_loop.adaptive_update_qt(np.array([0.5, 0.5]))
        return -np.linalg.norm(qt_state)  # lower magnitude = better

    def evolve_with_selection(self, psi_list, population_size=10):
        """
        Main evolution loop.
        """
        population = self._generate_population(population_size)

        scored = []
        for func in population:
            score = sum(self._score(psi, func) for psi in psi_list)
            scored.append((score, func))

        scored.sort(key=lambda x: x[0], reverse=True)

        elite = scored[:3]
        elite_funcs = [f for _, f in elite]

        self.engine.generated_functions = {
            f"elite_{i}": elite_funcs[i] for i in range(len(elite_funcs))
        }

        self.tracker.generation += 1


class EvolutionTracker:
    def __init__(self):
        self.generation = 0
