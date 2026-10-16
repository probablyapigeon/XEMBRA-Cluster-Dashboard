"""Empty initial registry; evolution workers maintain their own generated code."""
generated_functions = {}


def register(name, func):
    generated_functions[name] = func


def get(name):
    return generated_functions.get(name)
