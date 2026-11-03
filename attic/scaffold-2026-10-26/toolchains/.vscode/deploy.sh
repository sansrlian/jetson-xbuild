#!/bin/bash

# This script deploy specified host folder to target path.

RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NONE='\033[0m'

function print_usage() {
  echo -e "\n${GREEN}Usage${NONE}:"
  echo -e "  ${BOLD}./deploy.sh${NONE} TARGET_IP TARGET_USER SSHPASS SOURCE_FOLDER DEST_PATH"
  echo -e "\n${GREEN}TARGET_IP${NONE}:"
  echo -e "  ${NONE}IP address of remote target board."
  echo -e "\n${GREEN}TARGET_USER${NONE}:"
  echo -e "  ${NONE}User name."
  echo -e "\n${GREEN}SSHPASS${NONE}:"
  echo -e "  ${NONE}Password for ${TARGET_USER} user."
  echo -e "\n${GREEN}SOURCE_FOLDER${NONE}:"
  echo -e "  ${NONE}Path to the folder on host to deploy."
  echo -e "\n${GREEN}DEST_PATH${NONE}:"
  echo -e "  ${NONE}Destination path on target where the folder will be copied."
  echo -e "\n${GREEN}Examples${NONE}:"
  echo -e "  ${BOLD}./deploy.sh${NONE} 192.168.1.100 ubuntu mypass ./install /home/user/ros2_ws"
}

# Check if there are exactly 5 arguments
if [ $# -ne 5 ]; then
  echo -e "${RED}[Host] Incorrect number of arguments.${NONE}"
  print_usage
  exit 1
fi

TARGET_IP=$1
TARGET_USER=$2
SSHPASS=$3
SOURCE_FOLDER=$4
DEST_PATH=$5

# Check if source folder exists
if [ ! -d "$SOURCE_FOLDER" ]; then
  echo -e "${RED}[Host] Source folder '$SOURCE_FOLDER' does not exist.${NONE}"
  exit 1
fi

# Get absolute path of source folder
SOURCE_FOLDER_ABS="$(cd "$SOURCE_FOLDER" && pwd)"
SOURCE_FOLDER_NAME="$(basename "$SOURCE_FOLDER_ABS")"

echo -e "${GREEN}[Host] Preparing to deploy '${SOURCE_FOLDER_ABS}' to ${TARGET_USER}@${TARGET_IP}:${DEST_PATH}${NONE}"

# Check ssh connection
echo -e "${GREEN}[Host] Checking SSH connection...${NONE}"
if ! sshpass -p "${SSHPASS}" ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=accept-new ${TARGET_USER}@${TARGET_IP} "exit"; then
  echo -e "${RED}[Host] SSH connection to ${TARGET_USER}@${TARGET_IP} failed. Please check the IP address and password.${NONE}"
  exit 1
fi

# Create destination directory on target if it doesn't exist
echo -e "${GREEN}[Host] Creating destination directory on target...${NONE}"
if ! sshpass -p "${SSHPASS}" ssh ${TARGET_USER}@${TARGET_IP} "mkdir -p ${DEST_PATH}"; then
  echo -e "${RED}[Host] Failed to create destination directory '${DEST_PATH}' on target.${NONE}"
  exit 1
fi

# Sync the source directory to the target.
DEST_FULL_PATH="${DEST_PATH}/${SOURCE_FOLDER_NAME}"

echo -e "${GREEN}[Host] Deploying '${SOURCE_FOLDER_ABS}' to ${TARGET_USER}@${TARGET_IP}:${DEST_PATH}/...${NONE}"
if sudo rsync -az --delete-during -e "sshpass -p "${SSHPASS}" ssh -o StrictHostKeyChecking=no" "$SOURCE_FOLDER_ABS" "${TARGET_USER}@${TARGET_IP}:${DEST_PATH}/"; then
  echo -e "${GREEN}[Host] Successfully deployed to '${DEST_FULL_PATH}'${NONE}"

  # List the contents of the deployed folder
  echo -e "${GREEN}[Host] Contents of deployed folder:${NONE}"
  sshpass -p "${SSHPASS}" ssh ${TARGET_USER}@${TARGET_IP} "ls -la ${DEST_FULL_PATH} | head -20"
else
  echo -e "${RED}[Host] Failed to copy folder to target.${NONE}"
  exit 1
fi

echo -e "${GREEN}[Host] Deployment completed successfully!${NONE}"
