import logging

region = 'us-west-2'
key_prefix = 'samvera/metadata'
test_key_prefix = 'samvera/metadata/test'

def xmltos3(source, file_name, region_fallback=False, prefix=key_prefix):
    # Imported here: boto3 takes a noticeable time to import, and bagit_builder.py
    # imports this module in every process (--jobs workers too) even when nothing is
    # uploaded.
    import boto3
    from botocore.exceptions import ClientError

    # A failed upload always raises (boto3 reports it as S3UploadFailedError, not
    # ClientError). Only the bucket lookup afterwards can fail with a ClientError: that
    # returns False, or with region_fallback builds the URL from the client's region.
    s3 = boto3.client('s3', region_name=region)
    try:
        print("- Upload XML file to S3: " + file_name)
        bucket_name = 'cubl-static'
        key = prefix + '/' + file_name
        uploadObject = s3.upload_file(source + file_name, bucket_name, key)
        try:
            location = s3.get_bucket_location(Bucket=bucket_name)['LocationConstraint']
        except ClientError as e:
            if not region_fallback:
                raise
            print("- Bucket lookup failed (" + str(e) + "), using region " + region)
            location = region
        url = 'https://s3-{0}.amazonaws.com/{1}/{2}'.format(location, bucket_name, key)
        return url
    except ClientError as e:
        logging.error(e)
        return False
    return uploadObject
