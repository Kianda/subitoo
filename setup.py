from setuptools import setup

setup(
    name='subitoo',
    version='0.1.0',
    description='Price tracker and crawler for Subito.it',
    license='GPL v3',
    author='Kianda',
    packages=['src'],
    install_requires=["requests", "beautifulsoup4", "deepdiff", "tabulate", "tinydb", "django-ratelimit2", "setuptools"],
    entry_points={
        'console_scripts': [
            'subitoo-cmd=src.app:main']
    },
    classifiers=['Programming Language :: Python',
                 'Environment :: Console',
                 'Operating System :: POSIX :: Linux',
                 'License :: OSI Approved :: GNU General Public License v3 (GPLv3)'],
)
