import os
from setuptools import find_packages
from setuptools import setup

base_dir = os.path.dirname(__file__)
setup(
    name='vuegraf',
    version='1.6.1',
    author='Jason Ertel',
    url='https://github.com/jertel/vuegraf',
    description='Populate metrics from your Emporia Vue energy monitoring devices into an InfluxDB',
    setup_requires='setuptools',
    license='MIT',
    classifiers=[
        'Programming Language :: Python :: 3.12',
        'License :: OSI Approved :: MIT License',
        'Operating System :: OS Independent',
    ],
    entry_points={
        'console_scripts': ['vuegraf=vuegraf.vuegraf']
    },
    packages=find_packages(),
    install_requires=[
        'influxdb>=5.3.1',
        'influxdb_client>=1.33.0',
        'pyemvue>=0.16.0',
        'argparse>= 1.4.0'
    ]
)
