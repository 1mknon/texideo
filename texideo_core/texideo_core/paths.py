import os

_WORK_DIR = None

def set_work_dir(path):
    global _WORK_DIR
    _WORK_DIR = path

def get_work_dir():
    global _WORK_DIR
    if _WORK_DIR is None:
        _WORK_DIR = os.getcwd()
    return _WORK_DIR

def dna(filename=""):
    return os.path.join(get_work_dir(), "dna", filename)

def temp(filename=""):
    return os.path.join(get_work_dir(), "temp", filename)

def out(filename=""):
    return os.path.join(get_work_dir(), "out", filename)

def proj():
    return os.path.join(get_work_dir(), "projeto_edicao.txt")

def proj_hash():
    return os.path.join(get_work_dir(), "projeto_hash_edicao.txt")

def anchors_dir():
    return os.path.join(get_work_dir(), ".anchors")

def map_file():
    return os.path.join(get_work_dir(), ".anchors", "map.json")

def project_text():
    return os.path.join(get_work_dir(), "project.txt")

def project_order():
    return os.path.join(get_work_dir(), ".anchors", "project_order.json")