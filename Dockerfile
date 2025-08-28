FROM ubuntu:latest


# Update and install basic packages
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    git \
    wget \
    software-properties-common \
    sudo \ 
    libstdc++6  \
    && rm -rf /var/lib/apt/lists/*

# Add deadsnakes PPA and install Python 3.10
RUN add-apt-repository ppa:deadsnakes/ppa && \
    apt-get update && \
    apt-get install -y python3.10 python3.10-venv && \
    rm -rf /var/lib/apt/lists/*

RUN apt-get update
RUN sudo apt-get install libtool m4 automake -y



# Set the working directory in the container
WORKDIR /home/sash
CMD source /home/sash/.bashrc

#Install unzip 
RUN sudo apt-get update
RUN apt-get install unzip

#COPY z3 install script
COPY install_z3.sh /home/sash/install_z3.sh
RUN chmod +x /home/sash/install_z3.sh
RUN /home/sash/install_z3.sh


# Set Python 3.10 as the default python3
RUN update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.10 1

# Install pip for Python 3.10
RUN python3 -m ensurepip --upgrade

RUN pip3 install uv

SHELL ["/bin/bash", "-c"]

ADD . /home/sash
WORKDIR /home/sash
RUN uv pip install -e . --system
WORKDIR /home/sash
CMD ["/bin/bash"]
