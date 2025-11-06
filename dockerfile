# Use an official Python runtime as a parent image
FROM python:3.11-slim

# Set environment variables
ENV PYTHONUNBUFFERED True
ENV PORT 8080

# Set the working directory in the container
WORKDIR /app

# Copy the requirements file and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code
COPY . .

# Run the application using gunicorn, a production-ready HTTP server
# $PORT will be set by Cloud Run to 8080
CMD exec gunicorn --bind 0.0.0.0:$PORT --workers 1 main:app