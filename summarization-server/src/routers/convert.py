from fastapi import APIRouter, BackgroundTasks
from src.services.auth import verify_token, ErrorResponse
from fastapi import Depends, Form
from src.model.file_type import get_file_type, save_file, validate_audio, validate_doc, vtt_file
from werkzeug.utils import secure_filename
from pathlib import Path
from src.config import logger
from src.utils.env import _env
from src.utils.task import submit_task, task_status, task_process, ACTIVE_TASKS
from src.utils.file import get_audios, get_vtt
import os
import datetime, time
from src.db.pgvector_db import pgvectorDB
from src.model.data_model import Vtt
from src.utils.email import notify_vtt_finished

router = APIRouter(
    prefix="/api/v1"
)

@router.post("/audio-to-vtt")
async def audio_to_vtt(doc: str = Form(), token=Depends(verify_token)):
    return {'status': 'success', 'message': 'audio into convert queue'}

@router.post("/audio-to-vtt/complete")
# async def audio_task_notification(data: Vtt, background_tasks:BackgroundTasks,token=Depends(verify_jks)):
async def audio_task_notification(data: Vtt, background_tasks:BackgroundTasks):
    logger.info(f'--receive notification--{data.user}---{data.audio}')
    # email notify
    notify_vtt_finished(data.user, os.path.basename(data.audio))
    # waite vtt file write file system
    time.sleep(30)
    # create index
    # TODO: doc_id?
    background_tasks.add_task(pgvectorDB.vector_index, os.path.basename(data.vtt_path), data.user)


@router.get("/convert-process")
async def get_process(audio: str, token=Depends(verify_token)):
    return task_process(audio, token.username)


@router.get("/task-status")
async def get_task_status():
    return task_status()


@router.get("/vtt")
async def vtts(token=Depends(verify_token)):
    audios = get_audios(token.username)
    result = []
    modified_time = 0
    for audio in audios:
        vtt = get_vtt(audio, token.username)
        vtt_name = ''
        time = ''
        status = 'waiting'
        if vtt.exists():
            vtt_name = vtt.name
            modified_time = vtt.stat().st_mtime
            modified_datetime = datetime.datetime.fromtimestamp(modified_time)
            time = modified_datetime.strftime("%Y-%m-%d %H:%M:%S")
            status = 'done'
        else:
            server_config = _env.get_server_values()
            audio_path = Path(f"{server_config['FILE_PATH']}/{token.username}/{audio.name}")
            if str(audio_path) in ACTIVE_TASKS:
                status = 'in progress'
        result.append({'audio': audio.name, 'vtt': vtt_name, 'status': status, 'time': time})
    return {'data': result}
