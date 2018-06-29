import logging

from django.contrib.auth.decorators import login_required
from django.contrib.contenttypes.models import ContentType
from django.http import HttpResponse
from django.utils.functional import cached_property
from django.views.generic import View
from admin_resumable.files import ResumableFile


logger = logging.getLogger(__name__)


class UploadView(View):
    # inspired by another fork https://github.com/fdemmer/django-admin-resumable-js

    @cached_property
    def request_data(self):
        return getattr(self.request, self.request.method)

    @cached_property
    def model_upload_field(self):
        content_type = ContentType.objects.get_for_id(self.request_data['content_type_id'])
        return content_type.model_class()._meta.get_field(self.request_data['field_name'])

    def post(self, request, *args, **kwargs):
        chunk = request.FILES.get('file')
        r = ResumableFile(self.model_upload_field, user=request.user, params=request.POST)
        if not r.chunk_exists:
            logging.debug("POST chunk processing: %s", r.current_chunk_name)
            r.process_chunk(chunk)
        if r.is_complete:
            logging.debug("POST chunk uploads complete: %s", r.filename)
            completed = r.post_complete()
            return HttpResponse(completed)
        logging.debug("POST chunked uploaded: %s", r.current_chunk_name)
        return HttpResponse('chunk uploaded: %s' % r.current_chunk_name)

    def get(self, request, *args, **kwargs):
        r = ResumableFile(self.model_upload_field, user=request.user, params=request.GET)
        if not r.chunk_exists:
            logging.debug("POST chunk processing: %s", r.current_chunk_name)
            return HttpResponse('chunk not found', status=404)
        if r.is_complete:
            logging.debug("GET chunk uploads complete: %s", r.filename)
            completed = r.get_complete()
            return HttpResponse(completed)
        logging.debug("GET chunk exists: %s", r.current_chunk_name)
        return HttpResponse('chunk exists: %s' % r.current_chunk_name)


admin_resumable = login_required(UploadView.as_view())
