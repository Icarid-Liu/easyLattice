FROM sagemath/sagemath:10.4

USER root
WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY . /app

RUN git clone --depth=1 https://github.com/malb/lattice-estimator.git /opt/lattice-estimator

ENV PORT=7860
ENV SAGE_BINARY=sage
ENV LATTICE_ESTIMATOR_PATH=/opt/lattice-estimator
ENV EASYLATTICE_ESTIMATOR_TIMEOUT_SECONDS=240
ENV EASYLATTICE_ESTIMATOR_MAX_TIMEOUT_SECONDS=300
ENV EASYLATTICE_ESTIMATOR_WORKERS=1
ENV EASYLATTICE_ALLOWED_ORIGINS=*

EXPOSE 7860

CMD ["python3", "deploy/huggingface-estimator/space_app.py"]
