import setuptools

with open('README.rst') as fp:
    long_description = fp.read()

setuptools.setup(
    name='raw_zlib',
    version='0.1.15',
    author='mephi42',
    author_email='mephi42@gmail.com',
    description='Thin Python 3 wrapper around zlib',
    long_description=long_description,
    url='https://github.com/mephi42/raw_zlib',
    packages=setuptools.find_packages(),
    classifiers=[
        'Programming Language :: Python',
        'License :: OSI Approved :: BSD License',
        'Operating System :: POSIX',
    ],
)
