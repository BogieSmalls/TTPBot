from setuptools import setup, find_packages

setup(
    name='ttpbot',
    version='1.0.0',
    description='TTP scheduler bot for Z1R races',
    license='MIT',
    python_requires='>=3.10',
    install_requires=[
        'racetime_bot==2.3.0',
        'tzdata>=2026.3; platform_system=="Windows"',
    ],
    packages=find_packages(),
    package_data={'ttpbot.league': ['roster.json']},
    include_package_data=True,
    entry_points={
        'console_scripts': [
            'ttpbot=ttpbot:main',
        ],
    },
)
