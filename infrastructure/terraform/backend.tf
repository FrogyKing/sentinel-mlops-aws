# Bootstrap this private encrypted state bucket separately, with spending approval.
# Pass bucket/key/region at init time; never put credentials in backend configuration.
terraform {
  backend "s3" {}
}
