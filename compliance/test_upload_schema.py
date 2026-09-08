from django.test import SimpleTestCase
from drf_spectacular.generators import SchemaGenerator


class DocumentUploadSchemaTests(SimpleTestCase):
    def test_document_upload_exposes_required_binary_file_and_document_type(self):
        schema = SchemaGenerator().get_schema(public=True)
        operation = schema['paths']['/api/v1/compliance-applications/{id}/documents/']
        content = operation['post']['requestBody']['content']
        self.assertEqual(set(content), {'multipart/form-data'})
        reference = content['multipart/form-data']['schema']['$ref'].split('/')[-1]
        upload = schema['components']['schemas'][reference]
        self.assertEqual(set(upload['properties']), {'document_type', 'file'})
        self.assertEqual(set(upload['required']), {'document_type', 'file'})
        self.assertEqual(upload['properties']['file']['type'], 'string')
        self.assertEqual(upload['properties']['file']['format'], 'binary')
        self.assertIn('200', operation['post']['responses'])
        self.assertIn('201', operation['post']['responses'])
        listing = operation['get']['responses']['200']['content']['application/json']['schema']
        self.assertEqual(listing['type'], 'array')
