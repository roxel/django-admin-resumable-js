# -*- coding: utf-8 -*-
import os
import fnmatch
import tempfile
import logging

from django.utils.module_loading import import_string
from django.conf import settings
from django.core.files import File
from django.utils.functional import cached_property

from admin_resumable.storage import ResumableStorage


logger = logging.getLogger(__name__)


class ResumableFileHandler(object):
    """
    Base class for ResumableFile.
    When ResumableFile is mostly concerned with handling current upload,
    ResumableFileHandler is a helper for more general needs, for cases when only configuration and filename are known.
    Based on obeyed conventions it allows to locate chunks, read or collect them.
    """

    def __init__(self, storage_filename, chunk_storage, persistent_storage, chunk_suffix=None, chunk_dirname=None):
        self.storage_filename = storage_filename
        self.chunk_storage = chunk_storage
        self.persistent_storage = persistent_storage
        self.chunk_suffix = chunk_suffix or "_part_"
        self.chunk_dirname = chunk_dirname or "resumable_chunks/"

    @property
    def dirname(self):
        return os.path.dirname(self.storage_filename)

    @property
    def filename(self):
        return os.path.basename(self.storage_filename)

    def chunk_path(self, chunk_name):
        return os.path.join(self.chunk_dirname, chunk_name)

    @property
    def chunk_names(self):
        """
        Iterates over all stored chunks.
        """
        chunks = []
        logger.info("CHUNK NAMES: dirname = %s", self.chunk_dirname)
        files = sorted(self.chunk_storage.listdir(self.chunk_dirname)[1])
        for file in files:
            if fnmatch.fnmatch(file, '%s%s*' % (self.filename,
                                                self.chunk_suffix)):
                chunks.append(file)
        return chunks

    def chunks(self):
        """
        Iterates over all stored chunks.
        """
        # TODO: add user identifier to chunk name
        files = sorted(self.chunk_storage.listdir(self.chunk_dirname)[1])
        for file in files:
            if fnmatch.fnmatch(file, '%s%s*' % (self.filename,
                                                self.chunk_suffix)):
                yield self.chunk_storage.open(file, 'rb').read()

    def delete_chunks(self):
        for chunk in self.chunk_names:
            self.chunk_storage.delete(self.chunk_path(chunk))

    @property
    def file(self):
        outfile = tempfile.NamedTemporaryFile("w+b")
        for chunk in self.chunk_names:
            outfile.write(self.chunk_storage.open(self.chunk_path(chunk)).read())
        return outfile

    def collect(self, replace=False):
        if replace:
            self.persistent_storage.delete(self.storage_filename)
        actual_filename = self.persistent_storage.save(self.storage_filename, File(self.file))
        self.delete_chunks()
        return actual_filename


class ResumableFile(ResumableFileHandler):
    """
    Handles file saving and processing.
    It must only have access to chunk storage where it saves file chunks.
    When all chunks are uploaded it collects and merges them returning temporary file pointer
    that can be used to save the complete file to persistent storage.

    Chunk storage should preferably be some local storage to avoid traffic
    as files usually must be downloaded to server as chunks and re-uploaded as complete files.
    """

    def __init__(self, field, user, params):
        self.field = field
        self.user = user
        self.params = params
        self.chunk_suffix = "_part_"
        self.chunk_dirname = "resumable_chunks/"

    @cached_property
    def resumable_storage(self):
        return ResumableStorage()

    @cached_property
    def persistent_storage(self):
        return self.resumable_storage.get_persistent_storage()

    @cached_property
    def chunk_storage(self):
        return self.resumable_storage.get_chunk_storage()

    @cached_property
    def storage_filename(self):
        return self.resumable_storage.full_filename(self.filename, self.field.upload_to)

    def chunk_path(self, chunk_name=None):
        chunk_name = chunk_name or self.current_chunk_name
        return super(ResumableFile, self).chunk_path(chunk_name)

    @property
    def chunk_exists(self):
        """
        Checks if the requested chunk exists in chunk storage.
        If chunk with the same name exists but has different size it may be overwritten.
        """
        return self.chunk_storage.exists(self.chunk_path()) and \
               self.chunk_storage.size(self.chunk_path()) == int(self.params.get('resumableCurrentChunkSize'))

    @property
    def current_chunk_name(self):
        """
        Chunk partially unique name. Uniqueness should be based on file name and chunk number.
        We will assume that if chunk has the same name and number as the one existing
        then it has the same contents and should not be uploaded.
        """
        # TODO: add user identifier to chunk name
        # TODO: add timestamp for easier chunk cleaning for cancelled uploads
        return "%s%s%s" % (
            self.filename,
            self.chunk_suffix,
            self.params.get('resumableChunkNumber').zfill(4)
        )

    @property
    def file(self):
        """
        Merges file and returns its file pointer.
        """
        if not self.is_complete:
            raise Exception('Chunk(s) still missing')
        return super(ResumableFile, self).file

    @property
    def filename(self):
        """
        Gets the filename.
        """
        # TODO: add user identifier to chunk name
        filename = self.params.get('resumableFilename')
        if '/' in filename:
            raise Exception('Invalid filename')
        value = "%s_%s" % (self.params.get('resumableTotalSize'), filename)
        return value

    @property
    def is_complete(self):
        """
        Checks if all chunks are already stored.
        """
        return int(self.params.get('resumableTotalSize')) == self.size

    def process_chunk(self, file):
        """
        Saves chunk to chunk storage.
        """
        if self.chunk_storage.exists(self.chunk_path()):
            self.chunk_storage.delete(self.chunk_path())
        self.chunk_storage.save(self.chunk_path(), file)

    @property
    def size(self):
        """
        Gets size of all chunks combined.
        """
        size = 0
        for chunk_name in self.chunk_names:
            size += self.chunk_storage.size(self.chunk_path(chunk_name))
        return size

    def post_complete(self):
        """
        Action called when responding to HTTP POST request if chunk upload is already complete.
        """
        post_complete_callback = getattr(settings, "ADMIN_RESUMABLE_UPLOAD_POST_COMPLETE", None)
        if post_complete_callback:
            return import_string(post_complete_callback)(self)
        return self.collect()

    def get_complete(self):
        """
        Action called when responding to HTTP GET request if chunk upload is already complete.
        """
        get_complete_callback = getattr(settings, "ADMIN_RESUMABLE_UPLOAD_GET_COMPLETE", None)
        if get_complete_callback:
            return import_string(get_complete_callback)(self)
        return self.collect()
