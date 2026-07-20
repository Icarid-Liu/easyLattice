FROM sagemath/sagemath:10.4

USER root
WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY . /app

RUN git init /opt/lattice-estimator \
    && git -C /opt/lattice-estimator remote add origin https://github.com/malb/lattice-estimator.git \
    && git -C /opt/lattice-estimator fetch --depth=1 origin 3e48ef421ec256afddb3e7d2249a77eab6e9ba12 \
    && git -C /opt/lattice-estimator checkout --detach FETCH_HEAD
RUN git init /opt/enhanced-lattice-estimator \
    && git -C /opt/enhanced-lattice-estimator remote add origin https://github.com/identitymapping/enhanced_lattice-estimator.git \
    && git -C /opt/enhanced-lattice-estimator fetch --depth=1 origin 876b66173f4354a96ddafc0ce3a79767ec43c6d4 \
    && git -C /opt/enhanced-lattice-estimator checkout --detach FETCH_HEAD

ENV PORT=7860
ENV SAGE_BINARY=sage
ENV LATTICE_ESTIMATOR_PATH=/opt/lattice-estimator
ENV ENHANCED_LATTICE_ESTIMATOR_PATH=/opt/enhanced-lattice-estimator
ENV EASYLATTICE_ESTIMATOR_TIMEOUT_SECONDS=240
ENV EASYLATTICE_ESTIMATOR_MAX_TIMEOUT_SECONDS=300
ENV EASYLATTICE_ESTIMATOR_WORKERS=1
ENV EASYLATTICE_ALLOWED_ORIGINS=*

EXPOSE 7860

CMD ["python3", "deploy/huggingface-estimator/space_app.py"]
