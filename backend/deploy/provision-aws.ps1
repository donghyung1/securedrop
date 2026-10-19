param(
    [string]$Profile = 'default',
    [string]$Region = 'ap-northeast-2',
    [string]$BucketName = ''
)

$ErrorActionPreference = 'Stop'
$backendDirectory = Split-Path -Parent $PSScriptRoot
$configurationDirectory = Join-Path $PSScriptRoot 'aws'
New-Item -ItemType Directory -Path $configurationDirectory -Force | Out-Null
$utf8 = [System.Text.UTF8Encoding]::new($false)

function Invoke-Aws {
    param([string[]]$Arguments)
    $result = & aws @Arguments --profile $Profile --region $Region --no-cli-pager --output json
    if ($LASTEXITCODE -ne 0) { throw "AWS CLI failed: $($Arguments[0]) $($Arguments[1])" }
    if ($result) { return ($result -join "`n" | ConvertFrom-Json) }
}

function Write-Configuration {
    param([string]$Name, $Value)
    $path = Join-Path $configurationDirectory $Name
    [System.IO.File]::WriteAllText($path, ($Value | ConvertTo-Json -Depth 20), $utf8)
    return "file://$($path.Replace('\', '/'))"
}

$identity = Invoke-Aws -Arguments @('sts', 'get-caller-identity')
$accountId = $identity.Account
if (-not $BucketName) { $BucketName = "securedrop-cloud-$accountId-$Region" }
$bucketArn = "arn:aws:s3:::$BucketName"
$roleName = 'securedrop-cloud-backend'
$buckets = Invoke-Aws -Arguments @('s3api', 'list-buckets')
if ($BucketName -notin @($buckets.Buckets.Name)) {
    Invoke-Aws -Arguments @('s3api', 'create-bucket', '--bucket', $BucketName,
        '--create-bucket-configuration', "LocationConstraint=$Region") | Out-Null
}
# Verify ownership before changing the selected bucket.
Invoke-Aws -Arguments @('s3api', 'head-bucket', '--bucket', $BucketName,
    '--expected-bucket-owner', $accountId) | Out-Null

$publicAccess = Write-Configuration 'public-access.json' @{
    BlockPublicAcls = $true; IgnorePublicAcls = $true
    BlockPublicPolicy = $true; RestrictPublicBuckets = $true
}
Invoke-Aws -Arguments @('s3api', 'put-public-access-block', '--bucket', $BucketName,
    '--public-access-block-configuration', $publicAccess) | Out-Null

$ownership = Write-Configuration 'ownership.json' @{ Rules = @(@{ ObjectOwnership = 'BucketOwnerEnforced' }) }
Invoke-Aws -Arguments @('s3api', 'put-bucket-ownership-controls', '--bucket', $BucketName,
    '--ownership-controls', $ownership) | Out-Null

$encryption = Write-Configuration 'encryption.json' @{
    Rules = @(@{ ApplyServerSideEncryptionByDefault = @{ SSEAlgorithm = 'AES256' } })
}
Invoke-Aws -Arguments @('s3api', 'put-bucket-encryption', '--bucket', $BucketName,
    '--server-side-encryption-configuration', $encryption) | Out-Null

$lifecycle = Write-Configuration 'lifecycle.json' @{
    Rules = @(@{
        ID = 'expire-transfers-after-one-day'; Status = 'Enabled'
        Filter = @{ Prefix = 'transfers/' }; Expiration = @{ Days = 1 }
        AbortIncompleteMultipartUpload = @{ DaysAfterInitiation = 1 }
    })
}
Invoke-Aws -Arguments @('s3api', 'put-bucket-lifecycle-configuration', '--bucket', $BucketName,
    '--lifecycle-configuration', $lifecycle) | Out-Null

$bucketPolicy = Write-Configuration 'bucket-policy.json' @{
    Version = '2012-10-17'
    Statement = @(@{
        Sid = 'DenyInsecureTransport'; Effect = 'Deny'; Principal = '*'; Action = 's3:*'
        Resource = @($bucketArn, "$bucketArn/*")
        Condition = @{ Bool = @{ 'aws:SecureTransport' = 'false' } }
    })
}
Invoke-Aws -Arguments @('s3api', 'put-bucket-policy', '--bucket', $BucketName,
    '--policy', $bucketPolicy) | Out-Null

$tags = Write-Configuration 'tags.json' @{
    TagSet = @(@{ Key = 'Project'; Value = 'SecureDropCloud' }, @{ Key = 'ManagedBy'; Value = 'AWSCLI' })
}
Invoke-Aws -Arguments @('s3api', 'put-bucket-tagging', '--bucket', $BucketName,
    '--tagging', $tags) | Out-Null

$trust = Write-Configuration 'ec2-trust.json' @{
    Version = '2012-10-17'
    Statement = @(@{
        Effect = 'Allow'; Principal = @{ Service = 'ec2.amazonaws.com' }; Action = 'sts:AssumeRole'
    })
}
$roles = Invoke-Aws -Arguments @('iam', 'list-roles')
if ($roleName -notin @($roles.Roles.RoleName)) {
    Invoke-Aws -Arguments @('iam', 'create-role', '--role-name', $roleName,
        '--assume-role-policy-document', $trust,
        '--description', 'SecureDrop Cloud backend S3 access') | Out-Null
}
$objectPolicy = Write-Configuration 'backend-policy.json' @{
    Version = '2012-10-17'
    Statement = @(@{
        Sid = 'TransferObjectsOnly'; Effect = 'Allow'
        Action = @('s3:PutObject', 's3:GetObject', 's3:DeleteObject')
        Resource = "$bucketArn/transfers/*"
    })
}
Invoke-Aws -Arguments @('iam', 'put-role-policy', '--role-name', $roleName,
    '--policy-name', 'SecureDropTransferObjects', '--policy-document', $objectPolicy) | Out-Null

$profiles = Invoke-Aws -Arguments @('iam', 'list-instance-profiles')
if ($roleName -notin @($profiles.InstanceProfiles.InstanceProfileName)) {
    Invoke-Aws -Arguments @('iam', 'create-instance-profile', '--instance-profile-name', $roleName) | Out-Null
}
$instanceProfile = Invoke-Aws -Arguments @('iam', 'get-instance-profile', '--instance-profile-name', $roleName)
if ($roleName -notin @($instanceProfile.InstanceProfile.Roles.RoleName)) {
    Invoke-Aws -Arguments @('iam', 'add-role-to-instance-profile',
        '--instance-profile-name', $roleName, '--role-name', $roleName) | Out-Null
}

$envPath = Join-Path $backendDirectory '.env'
if (Test-Path -LiteralPath $envPath) {
    $envContent = [System.IO.File]::ReadAllText($envPath)
} else {
    $envContent = [System.IO.File]::ReadAllText((Join-Path $backendDirectory '.env.example'))
}
foreach ($setting in @{
    AWS_PROFILE = $Profile; AWS_REGION = $Region; S3_BUCKET = $BucketName
}.GetEnumerator()) {
    $pattern = '(?m)^' + [regex]::Escape($setting.Key) + '=.*$'
    $line = "$($setting.Key)=$($setting.Value)"
    if ([regex]::IsMatch($envContent, $pattern)) {
        $envContent = [regex]::Replace($envContent, $pattern, $line)
    } else { $envContent += "`n$line`n" }
}
if ($envContent -match '(?m)^PLUGIN_API_KEY=\s*$') {
    $random = [byte[]]::new(32)
    $generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $generator.GetBytes($random) } finally { $generator.Dispose() }
    $secret = [Convert]::ToBase64String($random).TrimEnd('=').Replace('+', '-').Replace('/', '_')
    $envContent = [regex]::Replace($envContent, '(?m)^PLUGIN_API_KEY=\s*$', "PLUGIN_API_KEY=$secret")
}
[System.IO.File]::WriteAllText($envPath, $envContent, $utf8)

$resources = @{
    account_id = $accountId; region = $Region; bucket = $BucketName
    role_arn = "arn:aws:iam::${accountId}:role/$roleName"
    instance_profile = $roleName; profile = $Profile
}
Write-Configuration 'resources.json' $resources | Out-Null
$resources | ConvertTo-Json
