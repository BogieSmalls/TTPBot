from setuptools import setup, find_packages

setup(
    name='ttpbot',
    version='1.0.0',
    description='TTP Season 4 bot for racetime.gg Z1R races',
    license='MIT',
    python_requires='>=3.9',
    install_requires=[
        'racetime_bot>=2.3.0,<3.0',
    ],
    packages=find_packages(),
    entry_points={
        'console_scripts': [
            'ttpbot=ttpbot:main',
        ],
    },
)
