import numpy as np
from qml2.kernels import gaussian_kernel_symmetric

np.random.seed(4)

sigma = 2.0
vectors = np.random.random((4, 8))

kernel = gaussian_kernel_symmetric(vectors, sigma)

print(kernel)
