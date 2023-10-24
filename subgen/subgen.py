import subprocess
import os
import json
import xml.etree.ElementTree as ET
import threading
import sys
import time
import queue
import logging

# List of packages to install
packages_to_install = [
    'numpy',
    'stable-ts',
    'flask',
    'requests',
    'faster-whisper',
    # Add more packages as needed
]

for package in packages_to_install:
    print(f"Installing {package}...")
    try:
        subprocess.run(['pip3', 'install', package], check=True)
        print(f"{package} has been successfully installed.")
    except subprocess.CalledProcessError as e:
        print(f"Failed to install {package}: {e}")

from flask import Flask, request    
import stable_whisper
import requests
import av

def convert_to_bool(in_bool):
    if isinstance(in_bool, bool):
        return in_bool
    else:
        value = str(in_bool).lower()
        return value not in ('false', 'off', '0')

# Replace your getenv calls with appropriate default values here
plextoken = os.getenv('PLEXTOKEN', "token here")
plexserver = os.getenv('PLEXSERVER', "http://192.168.1.111:32400")
jellyfintoken = os.getenv('JELLYFINTOKEN', "token here")
jellyfinserver = os.getenv('JELLYFINSERVER', "http://192.168.1.111:8096")
whisper_model = os.getenv('WHISPER_MODEL', "medium")
whisper_threads = int(os.getenv('WHISPER_THREADS', 4))
concurrent_transcriptions = int(os.getenv('CONCURRENT_TRANSCRIPTIONS', '2'))
transcribe_device = os.getenv('TRANSCRIBE_DEVICE', "cpu")
procaddedmedia = convert_to_bool(os.getenv('PROCADDEDMEDIA', True))
procmediaonplay = convert_to_bool(os.getenv('PROCMEDIAONPLAY', True))
namesublang = os.getenv('NAMESUBLANG', "aa")
skipifinternalsublang = os.getenv('SKIPIFINTERNALSUBLANG', "eng")
webhookport = int(os.getenv('WEBHOOKPORT', 8090))
word_level_highlight = convert_to_bool(os.getenv('WORD_LEVEL_HIGHLIGHT', False))
debug = convert_to_bool(os.getenv('DEBUG', False))
use_path_mapping = convert_to_bool(os.getenv('USE_PATH_MAPPING', False))
path_mapping_from = os.getenv('PATH_MAPPING_FROM', '/tv')
path_mapping_to = os.getenv('PATH_MAPPING_TO', '/Volumes/TV')
model_location = os.getenv('MODEL_PATH', '.')
if transcribe_device == "gpu":
    transcribe_device = "cuda"

app = Flask(__name__)
model = stable_whisper.load_faster_whisper(whisper_model, download_root=model_location, device=transcribe_device, cpu_threads=whisper_threads, num_workers=concurrent_transcriptions)
files_to_transcribe = set()
subextension =  '.subgen.' + whisper_model + '.' + namesublang + '.srt'
print("Transcriptions are limited to running " + str(concurrent_transcriptions) + " at a time")
print("Running " + str(whisper_threads) + " threads per transcription")
if debug:
    logging.basicConfig(stream=sys.stderr, level=logging.DEBUG)

@app.route("/webhook", methods=["POST"])
def print_warning():
    print("*** This is the legacy webhook.  You need to update to webhook urls to end in plex, tautulli, or jellyfin instead of webhook. ***")
    return ""

@app.route("/tautulli", methods=["POST"])
def receive_tautulli_webhook():
    logging.debug("This hook is from Tautulli webhook!")
    logging.debug("Headers: %s", request.headers)
    logging.debug("Raw response: %s", request.data)
    
    if request.headers.get("Source") == "Tautulli":
        event = request.json["event"]
        if((event == "added" and procaddedmedia) or (event == "played" and procmediaonplay)):
            fullpath = request.json["file"]
            logging.debug("Path of file: " + fullpath)
        
            if use_path_mapping:
                fullpath = fullpath.replace(path_mapping_from, path_mapping_to)
                logging.debug("Updated path: " + fullpath.replace(path_mapping_from, path_mapping_to))

            add_file_for_transcription(fullpath)
    else:
        print("This doesn't appear to be a properly configured Tautulli webhook, please review the instructions again!")
    
    return ""
    
@app.route("/plex", methods=["POST"])
def receive_plex_webhook():
    logging.debug("This hook is from Plex webhook!")
    logging.debug("Headers: %s", request.headers)
    logging.debug("Raw response: %s", json.loads(request.form['payload']))
    
    if "PlexMediaServer" in request.headers.get("User-Agent"):
        plex_json = json.loads(request.form['payload'])
        event = plex_json["event"]
        if((event == "library.new" and procaddedmedia) or (event == "media.play" and procmediaonplay)):
            fullpath = get_plex_file_name(plex_json['Metadata']['ratingKey'], plexserver, plextoken)
            logging.debug("Path of file: " + fullpath)
            
            if use_path_mapping:
                fullpath = fullpath.replace(path_mapping_from, path_mapping_to)
                logging.debug("Updated path: " + fullpath.replace(path_mapping_from, path_mapping_to))
     
            add_file_for_transcription(fullpath)
    else:
        print("This doesn't appear to be a properly configured Plex webhook, please review the instructions again!")
     
    return ""

@app.route("/jellyfin", methods=["POST"])
def receive_jellyfin_webhook():
    logging.debug("This hook is from Jellyfin webhook!")
    logging.debug("Headers: %s", request.headers)
    logging.debug("Raw response: %s", request.data)
    
    if "Jellyfin-Server" in request.headers.get("User-Agent"):
        event = request.json["NotificationType"]
        logging.debug(event)
        if((event == "ItemAdded" and procaddedmedia) or (event == "PlaybackStart" and procmediaonplay)):
            fullpath = get_jellyfin_file_name(request.json["ItemId"], jellyfinserver, jellyfintoken)
            logging.debug("Path of file: " + fullpath)
            
            if use_path_mapping:
                fullpath = fullpath.replace(path_mapping_from, path_mapping_to)
                logging.debug("Updated path: " + fullpath.replace(path_mapping_from, path_mapping_to))
     
            add_file_for_transcription(fullpath)
    else:
        print("This doesn't appear to be a properly configured Jellyfin webhook, please review the instructions again!")
     
    return ""

def gen_subtitles(video_file_path: str) -> None:
    try:
        print(f"Transcribing file: {video_file_path}")
        start_time = time.time()
        result = model.transcribe_stable(video_file_path)
        result.to_srt_vtt(video_file_path.rsplit('.', 1)[0] + subextension, word_level=word_level_highlight)
        elapsed_time = time.time() - start_time
        minutes, seconds = divmod(int(elapsed_time), 60)
        print(f"Transcription of {video_file_path} is completed, it took {minutes} minutes and {seconds} seconds to complete.")
    except Exception as e:
        print(f"Error processing or transcribing {video_file_path}: {e}")
    finally:
        files_to_transcribe.remove(video_file_path)

# Function to add a file for transcription
def add_file_for_transcription(file_path):
    if file_path not in files_to_transcribe:
        
        if has_subtitle_language(file_path, skipifinternalsublang):
            print("File already has an internal sub we want, skipping generation")
            return "File already has an internal sub we want, skipping generation"
        elif os.path.exists(file_path.rsplit('.', 1)[0] + subextension):
            print("We already have a subgen created for this file, skipping it")
            return "We already have a subgen created for this file, skipping it"
            
        files_to_transcribe.add(file_path)
        print(f"Added {file_path} for transcription.")
        # Start transcription for the file in a separate thread
    
        gen_subtitles(file_path)
        
    else:
        print(f"File {file_path} is already in the transcription list. Skipping.")

def has_subtitle_language(video_file, target_language):
    try:
        container = av.open(video_file)
        subtitle_stream = None

        # Iterate through the streams in the video file
        for stream in container.streams:
            if stream.type == 'subtitle':
                # Check if the subtitle stream has the target language
                if 'language' in stream.metadata and stream.metadata['language'] == target_language:
                    subtitle_stream = stream
                    break

        if subtitle_stream:
            print(f"Subtitles in '{target_language}' language found in the video.")
            return True
        else:
            print(f"No subtitles in '{target_language}' language found in the video.")

        container.close()
    except Exception as e:
        print(f"An error occurred: {e}")
        return False
    
def get_plex_file_name(itemid: str, server_ip: str, plex_token: str) -> str:
    """Gets the full path to a file from the Plex server.

    Args:
        itemid: The ID of the item in the Plex library.
        server_ip: The IP address of the Plex server.
        plex_token: The Plex token.

    Returns:
        The full path to the file.
    """

    url = f"{server_ip}/library/metadata/{itemid}"

    headers = {
        "X-Plex-Token": plex_token,
    }

    response = requests.get(url, headers=headers)

    if response.status_code == 200:
        root = ET.fromstring(response.content)
        fullpath = root.find(".//Part").attrib['file']
        return fullpath
    else:
        raise Exception(f"Error: {response.status_code}")

def get_jellyfin_file_name(item_id: str, jellyfin_url: str, jellyfin_token: str) -> str:
    """Gets the full path to a file from the Jellyfin server.

    Args:
        jellyfin_url: The URL of the Jellyfin server.
        jellyfin_token: The Jellyfin token.
        item_id: The ID of the item in the Jellyfin library.

    Returns:
        The full path to the file.
    """

    headers = {
        "Authorization": f"MediaBrowser Token={jellyfin_token}",
    }

    # This is super hacky to pull the first userid from this file to use to make the next call, API appears to fail using only Items/{item_id}
    userid = json.loads(requests.get(f"{jellyfin_url}/Users", headers=headers).content)[0]['Id']
    response = requests.get(f"{jellyfin_url}/Users/{userid}/Items/{item_id}", headers=headers)

    if response.status_code == 200:
        json_data = json.loads(response.content)
        file_name = json_data['Path']
        return file_name
    else:
        raise Exception(f"Error: {response.status_code}")

print("Starting webhook!")
if __name__ == "__main__":
    app.run(debug=debug, host='0.0.0.0', port=int(webhookport))
