FROM sagemath/sagemath:10.4

USER root
WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY . /app

RUN git clone --depth=1 https://github.com/malb/lattice-estimator.git /opt/lattice-estimator
RUN git init /opt/enhanced-lattice-estimator \
    && git -C /opt/enhanced-lattice-estimator remote add origin https://github.com/identitymapping/enhanced_lattice-estimator.git \
    && git -C /opt/enhanced-lattice-estimator fetch --depth=1 origin 876b66173f4354a96ddafc0ce3a79767ec43c6d4 \
    && git -C /opt/enhanced-lattice-estimator checkout --detach FETCH_HEAD

ENV HOST=0.0.0.0
ENV PORT=7860
ENV SAGE_BINARY=sage
ENV LATTICE_ESTIMATOR_PATH=/opt/lattice-estimator
ENV ENHANCED_LATTICE_ESTIMATOR_PATH=/opt/enhanced-lattice-estimator
ENV EASYLATTICE_ESTIMATOR_TIMEOUT=240
ENV EASYLATTICE_ESTIMATOR_PER_ATTACK_TIMEOUT=60
ENV EASYLATTICE_ALLOWED_ORIGINS=*

EXPOSE 7860

CMD ["python3", "-m", "app.server"]
