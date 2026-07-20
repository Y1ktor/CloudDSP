FROM public.ecr.aws/lambda/python:3.11

# Install tar and xz (required for extracting the static ffmpeg build)
RUN yum update -y && \
    yum install -y tar xz wget && \
    yum clean all

# Download and install a static build of FFmpeg
RUN wget https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz && \
    tar -xJf ffmpeg-release-amd64-static.tar.xz && \
    mv ffmpeg-*-amd64-static/ffmpeg /usr/local/bin/ffmpeg && \
    mv ffmpeg-*-amd64-static/ffprobe /usr/local/bin/ffprobe && \
    rm -rf ffmpeg-*-amd64-static*

# Copy requirements and install dependencies
COPY requirements.txt ${LAMBDA_TASK_ROOT}
RUN pip install --no-cache-dir --upgrade -r requirements.txt
RUN pip install --no-cache-dir --upgrade "yt-dlp[default,curl-cffi]"

# Copy the lambda function handler
COPY LambdaYtDlp.py ${LAMBDA_TASK_ROOT}

# Set the CMD to your handler
CMD [ "LambdaYtDlp.lambda_handler" ]
