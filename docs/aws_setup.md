# AWS setup (optional)

IncidentIQ runs fully on a laptop. These steps add S3 storage and a small Lambda that logs uploads
to CloudWatch. **Free-tier eligibility varies by account and region. Check yours, and follow
step 0 before you create anything.** Upload only the synthetic files produced by this project.

## 0. Create a budget alert first

1. Sign in to the AWS console with your root or admin user.
2. Search **Billing and Cost Management** → **Budgets** → **Create budget**.
3. Choose **Use a template** → **Zero spend budget** (or a monthly cost budget of $1).
4. Enter your email address → **Create budget**. You will get an email as soon as anything costs money.

## 1. Create the S3 bucket

1. Console → **S3** → **Create bucket**.
2. Bucket name: something globally unique, e.g. `incidentiq-demo-<your-initials>-<random>`.
3. Region: the same one you will put in `.env` (e.g. `us-east-1`).
4. Keep **Block all public access** switched **on**. Leave the other defaults → **Create bucket**.

## 2. Create an IAM user that can only use that bucket

1. Console → **IAM** → **Policies** → **Create policy** → **JSON** tab, paste (replace the bucket name):

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {"Effect": "Allow", "Action": ["s3:ListBucket"],
     "Resource": "arn:aws:s3:::YOUR-BUCKET-NAME"},
    {"Effect": "Allow", "Action": ["s3:PutObject", "s3:GetObject"],
     "Resource": "arn:aws:s3:::YOUR-BUCKET-NAME/*"}
  ]
}
```

2. Name it `IncidentIQ-S3-Only` → **Create policy**.
3. **IAM** → **Users** → **Create user** → name `incidentiq-app` → **Attach policies directly** →
   tick `IncidentIQ-S3-Only` → **Create user**.
4. Open the user → **Security credentials** → **Create access key** → *Local code* → copy both keys.
5. On your laptop either run `aws configure --profile incidentiq` (needs the AWS CLI), or put the
   keys in `.env` as `AWS_ACCESS_KEY_ID=` / `AWS_SECRET_ACCESS_KEY=`. Never commit them.
6. In `.env` set `S3_BUCKET=your-bucket-name`, `AWS_REGION=...` and, if you used a profile,
   `AWS_PROFILE=incidentiq`.
7. Test: `python src/cloud_s3.py` should print `S3 enabled`. Then
   `python src/pipeline.py --upload` or the **Cloud** page uploads files.

## 3. Deploy the Lambda

1. Console → **Lambda** → **Create function** → *Author from scratch*.
   Name `incidentiq-upload-logger`, runtime **Python 3.12**, architecture x86_64 → **Create function**.
2. In the code editor replace `lambda_function.py` with the contents of `lambda/handler.py`
   (or rename the file to `handler.py` and set **Runtime settings → Handler** to `handler.lambda_handler`).
   Click **Deploy**.
3. **Configuration** → **Permissions** → click the role name → **Add permissions** → **Create inline
   policy** → JSON: allow `s3:GetObject` on `arn:aws:s3:::YOUR-BUCKET-NAME/*` → save.
   (The role already has permission to write CloudWatch logs.)
4. **Configuration** → **General configuration** → timeout 30 seconds, memory 128 MB.

## 4. Add the S3 trigger

1. In the function → **Add trigger** → **S3** → choose your bucket.
2. Event types: **All object create events**. Suffix: `.csv`.
3. Tick the recursive-invocation acknowledgement (the function only reads, it never writes to the
   bucket) → **Add**.

## 5. See it work in CloudWatch

1. Upload a file: Cloud page → prefix `raw/` → select `incidents_dirty_test.csv` → **Upload selected**.
2. Lambda → **Monitor** → **View CloudWatch logs** → newest log stream. You should see a line like
   `{"bucket": "...", "key": "raw/incidents_dirty_test.csv", "size_bytes": 95119, "columns": 18, "row_count": 200}`.
3. Take a screenshot for the demo.

## 6. Delete everything afterwards

1. **S3** → bucket → **Empty** → confirm → then **Delete** the bucket.
2. **Lambda** → select the function → **Actions → Delete**.
3. **CloudWatch** → **Log groups** → delete `/aws/lambda/incidentiq-upload-logger`.
4. **IAM** → delete the access key, the user `incidentiq-app`, the policy `IncidentIQ-S3-Only` and the
   Lambda's execution role.
5. Keep the budget alert, or delete it last.
