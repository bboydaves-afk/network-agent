"""Network Agent -- setup.py for pip-installable distribution."""

from setuptools import setup, find_packages

with open("requirements.txt", encoding="utf-8") as fh:
    install_requires = [
        line.strip()
        for line in fh
        if line.strip() and not line.strip().startswith("#")
    ]

setup(
    name="network-agent",
    version="1.0.0",
    description="AI-powered Network Engineer Agent for managing onsite networks",
    long_description=(
        "A comprehensive network management tool that combines device "
        "configuration management, real-time health monitoring, automated "
        "discovery, and an AI-powered chat interface for natural-language "
        "network operations."
    ),
    author="Network Agent",
    python_requires=">=3.10",
    packages=find_packages(exclude=["tests", "tests.*"]),
    include_package_data=True,
    install_requires=install_requires,
    extras_require={
        "dev": [
            "httpx>=0.27.0",       # Required by FastAPI TestClient
        ],
    },
    entry_points={
        "console_scripts": [
            "netagent=run:main",
        ],
    },
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: System Administrators",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Programming Language :: Python :: 3.13",
        "Topic :: System :: Networking",
        "Topic :: System :: Networking :: Monitoring",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
)
