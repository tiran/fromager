CUDA_BASENAMES: set[str] = {
    "libcuda.so",
    "libcudart.so",
    # PyTorch
    "libtorch_cuda.so",
}

ROCM_BASENAMES: set[str] = {
    "libamdhip64.so",
    # additional candidates
    "libMIOpen.so",
    "libhipblas.so",
    "libhipblaslt.so",
    "libhipfft.so",
    "libhiprand.so",
    "libhipsolver.so",
    "libhipsparse.so",
    "libhipsparselt.so",
    "librccl.so",
    "librocblas.so",
    "librocrand.so",
    "librocsolver.so",
    # PyTorch
    "libtorch_hip.so",
}

PYTORCH_BASENAMES: set[str] = {
    "libtorch.so",
    "libtorch_cpu.so",
    "libtorch_cuda.so",
    "libtorch_hip.so",
    "libtorch_python.so",
}
