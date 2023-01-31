# Running this script will bring up containers for InfluxDB and Vuegraf. 
# Re-running this script will bring any previous containers down and bring up containers with any configuration changes you have made.
# Ran on MacOS with Docker Desktop for Mac 3.2.1
# vuegraf.json setting for host. "host": "host.docker.internal"

set -e

echo "Bringing up InfluxDB and Vuegraf"
docker-compose down && docker-compose -f docker-compose.yaml build --pull && docker-compose -f docker-compose.yaml up -d

echo "Done"
