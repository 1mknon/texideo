from setuptools import setup, find_packages

setup(
    name='texideo_core',
    version='1.0.0',
    packages=find_packages(),
    install_requires=[
        'faster-whisper',
        'opentimelineio',
    ],
    python_requires='>=3.8',
)
