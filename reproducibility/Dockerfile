FROM python:3.12.11-slim-bookworm@sha256:519591d6871b7bc437060736b9f7456b8731f1499a57e22e6c285135ae657bf7

WORKDIR /runner
COPY requirements.lock /runner/requirements.lock
RUN awk '!/^pywin32==/' /runner/requirements.lock > /runner/requirements-linux.lock \
    && python -m pip install --no-cache-dir --requirement /runner/requirements-linux.lock
COPY reproducibility/clean_room_runner.py /runner/clean_room_runner.py
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONHASHSEED=0
ENTRYPOINT ["python", "/runner/clean_room_runner.py"]
