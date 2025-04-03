import os
from setuptools import find_packages
from setuptools import setup

base_dir = os.path.dirname(__file__)
setup(
    name='vuegraf',
    version='1.9.0',
    author='Jason Ertel',
    long_description=open('README.md').read(),
    long_description_content_type="text/markdown",
    url='https://github.com/jertel/vuegraf',
    description='Populate metrics from your Emporia Vue energy monitoring devices into an InfluxDB',
    setup_requires='setuptools',
    license='MIT',
    project_urls={
        "Documentation": "https://github.com/jertel/vuegraf",
        "Source Code": "https://github.com/jertel/vuegraf",
        "Discussion Forum": "https://github.com/jertel/vuegraf/discussions",
    },
    classifiers=[
        'Programming Language :: Python :: 3.12',
        'Operating System :: OS Independent',
    ],
    entry_points={
        'console_scripts': ['vuegraf=vuegraf.vuegraf:main']
    },
    packages=find_packages(where='src', exclude=['*_test*']),  # Exclude test files from the package
    package_dir={'':'src'},
    python_requires='>=3.12',
    install_requires=[
        'influxdb>=5.3.2',
        'influxdb_client>=1.48.0',
        'pyemvue>=0.18.7',
        'argparse>= 1.4.0'
    ]
)
