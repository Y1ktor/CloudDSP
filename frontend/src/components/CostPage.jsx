import './CostPage.css';

const pricingSources = {
    ec2: 'https://aws.amazon.com/ec2/pricing/on-demand/',
    batch: 'https://aws.amazon.com/batch/pricing/',
    vpc: 'https://aws.amazon.com/vpc/pricing/',
    ebs: 'https://aws.amazon.com/ebs/pricing/',
    lambda: 'https://aws.amazon.com/lambda/pricing/',
    apiGateway: 'https://aws.amazon.com/api-gateway/pricing/',
    dynamodb: 'https://aws.amazon.com/dynamodb/pricing/on-demand/',
    dynamodbRecovery: 'https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/DynamodbDisasterRecoveryStrategy.html',
    s3: 'https://aws.amazon.com/s3/pricing/',
    eventBridge: 'https://aws.amazon.com/eventbridge/pricing/',
    sqs: 'https://aws.amazon.com/sqs/pricing/',
    cloudWatch: 'https://aws.amazon.com/cloudwatch/pricing/',
    cloudFront: 'https://aws.amazon.com/cloudfront/pricing/pay-as-you-go/',
    cognito: 'https://aws.amazon.com/cognito/pricing/',
    ecr: 'https://aws.amazon.com/ecr/pricing/',
    route53: 'https://aws.amazon.com/route53/pricing/',
    kms: 'https://aws.amazon.com/kms/pricing/',
    systemsManager: 'https://aws.amazon.com/systems-manager/pricing/',
};

const perJobCosts = [
    {
        service: 'EC2 GPU through AWS Batch',
        usage: 'One g4dn.xlarge for 25 billed minutes',
        rate: '$0.526 / instance-hour',
        freeTier: 'No recurring GPU free tier',
        estimate: '$0.2192',
        source: pricingSources.ec2,
    },
    {
        service: 'Public IPv4',
        usage: 'One address for the same 25 minutes',
        rate: '$0.005 / address-hour',
        freeTier: 'No recurring allowance',
        estimate: '$0.0021',
        source: pricingSources.vpc,
    },
    {
        service: 'EBS root volume',
        usage: 'Estimated 30 GiB gp3 root volume for 25 minutes',
        rate: '$0.08 / GB-month',
        freeTier: 'New-account terms vary',
        estimate: '$0.0014',
        source: pricingSources.ebs,
    },
    {
        service: 'S3 artifacts',
        usage: 'About 0.75 GB retained for 14 days, plus roughly 19 writes and 27 reads',
        rate: '$0.023 / GB-month; request pricing by operation',
        freeTier: 'New-account terms vary',
        estimate: '$0.0082',
        source: pricingSources.s3,
    },
    {
        service: 'MIDI extraction Lambdas',
        usage: 'Five Basic Pitch invocations and one ADTOF invocation at 3,008 MiB',
        rate: '$0.0000166667 / GB-second + requests',
        freeTier: 'Shared 400,000 GB-s and 1M requests each month',
        estimate: '$0.0000 / $0.01362*',
        source: pricingSources.lambda,
    },
    {
        service: 'Job and WebSocket Lambdas',
        usage: 'Job creation, snapshot polling, authorization, subscription, and heartbeat work',
        rate: '$0.0000133334 / GB-second for the Arm ZIP functions',
        freeTier: 'Shares the Lambda account allowance',
        estimate: '$0.0000 / < $0.0003*',
        source: pricingSources.lambda,
    },
    {
        service: 'API Gateway',
        usage: 'About 150 HTTP calls, 25 WebSocket messages, and 25 connection-minutes',
        rate: '$1.00 / M HTTP calls; $1.00 / M messages; $0.25 / M connection-minutes',
        freeTier: 'Time-limited API free tier for eligible accounts',
        estimate: '~$0.00015*',
        source: pricingSources.apiGateway,
    },
    {
        service: 'DynamoDB on-demand',
        usage: 'Job, quota, connection, and polling reads/writes',
        rate: '$0.625 / M WRUs; $0.125 / M RRUs',
        freeTier: '25 GB table storage; on-demand requests remain metered',
        estimate: '~$0.00004*',
        source: pricingSources.dynamodb,
    },
    {
        service: 'EventBridge and SQS',
        usage: 'One S3 Object Created service event; DLQ is normally empty',
        rate: '$0 for the AWS service event; $0.40 / M standard SQS requests',
        freeTier: '1M SQS requests each month',
        estimate: '$0.0000 normally',
        source: pricingSources.eventBridge,
    },
    {
        service: 'CloudWatch Logs',
        usage: 'Batch and Lambda application logs with 30-day retention',
        rate: '$0.50 / ingested GB in us-east-1',
        freeTier: '5 GB of Logs usage each month',
        estimate: '$0.0000 within allowance',
        source: pricingSources.cloudWatch,
    },
];

const monthlySharedCosts = [
    {
        service: 'Amazon ECR',
        basis: '5.54 GB of tagged compressed worker images observed on 27 Aug 2026',
        estimate: '~$0.55 / month',
        note: 'Untagged layers can add storage. In-region pulls by EC2 and Lambda do not add ECR transfer charges.',
        source: pricingSources.ecr,
    },
    {
        service: 'Route 53 hosted zone',
        basis: 'One public hosted zone',
        estimate: '$0.50 / month',
        note: 'Alias queries to the CloudFront distribution are free. Domain registration is separate.',
        source: pricingSources.route53,
    },
    {
        service: 'Website and demo S3 storage',
        basis: 'Current small static build and curated demo catalog',
        estimate: '~$0.02 / month',
        note: 'Grows with demo WAV and MIDI assets. CloudFront origin transfer is not charged.',
        source: pricingSources.s3,
    },
    {
        service: 'Proxy SecureString KMS key',
        basis: 'Only when the optional yt-dlp proxy parameter is configured',
        estimate: '$0 now; +$1.00 / month when enabled',
        note: 'Standard Parameter Store itself has no additional charge; KMS key storage does.',
        source: pricingSources.kms,
    },
    {
        service: 'DynamoDB PITR and retained logs',
        basis: 'Actual table bytes and log volume',
        estimate: 'Usage-dependent',
        note: 'PITR is $0.20/GB-month. It is small in development, but not safely attributable to one job or user.',
        source: pricingSources.dynamodbRecovery,
    },
];

const scaleRates = [
    {
        service: 'AWS Batch',
        included: 'The scheduler has no additional service charge.',
        beyond: 'Pay for EC2, EBS, IPv4, logs, and other resources launched for jobs.',
        source: pricingSources.batch,
    },
    {
        service: 'Lambda',
        included: '1M requests and 400,000 GB-seconds per month, shared across the account.',
        beyond: '$0.20/M requests, $0.0000166667/GB-second for x86, and $0.0000000309/GB-second of /tmp above 512 MiB.',
        source: pricingSources.lambda,
    },
    {
        service: 'API Gateway HTTP + WebSocket',
        included: 'Eligible new accounts receive 1M HTTP calls, 1M WebSocket messages, and 750,000 connection-minutes monthly for up to 12 months.',
        beyond: '$1.00/M HTTP calls, $1.00/M messages, and $0.25/M connection-minutes in us-east-1.',
        source: pricingSources.apiGateway,
    },
    {
        service: 'Cognito Lite',
        included: '10,000 direct or social-provider MAUs per month, indefinitely.',
        beyond: '$0.0055 per MAU for the next 90,000 billable MAUs; one user is counted once per active month.',
        source: pricingSources.cognito,
    },
    {
        service: 'CloudFront pay-as-you-go',
        included: '1 TB transfer, 10M HTTP/HTTPS requests, and 2M Function invocations each month.',
        beyond: 'Price Class 100 starts around $0.085/GB and $0.010/10,000 HTTPS requests; Functions are $0.10/M and invalidations are $0.005/path after their allowances.',
        source: pricingSources.cloudFront,
    },
    {
        service: 'S3 Standard',
        included: 'The first 100 GB/month of regional internet transfer is shared across eligible AWS services; new-account storage/request offers vary.',
        beyond: '$0.023/GB-month, $0.005/1,000 writes, $0.0004/1,000 reads, and about $0.09/GB direct internet transfer.',
        source: pricingSources.s3,
    },
    {
        service: 'DynamoDB on-demand',
        included: '25 GB of Standard table storage.',
        beyond: '$0.625/M WRUs, $0.125/M RRUs, $0.25/GB-month table storage; PITR is separate.',
        source: pricingSources.dynamodb,
    },
    {
        service: 'SQS + EventBridge',
        included: '1M SQS requests/month; the CloudDSP S3 state-change service event is free.',
        beyond: '$0.40/M standard SQS requests. Custom EventBridge events would be $1.00/M if introduced.',
        source: pricingSources.sqs,
    },
    {
        service: 'CloudWatch Logs',
        included: '5 GB of combined Logs ingestion, archive, and query scan each month.',
        beyond: '$0.50/GB ingested in us-east-1, plus storage and query charges.',
        source: pricingSources.cloudWatch,
    },
    {
        service: 'ECR private repositories',
        included: '500 MB/month for one year for eligible new ECR customers.',
        beyond: '$0.10/GB-month; same-Region pulls to EC2 and Lambda are free.',
        source: pricingSources.ecr,
    },
    {
        service: 'KMS + Parameter Store',
        included: '20,000 KMS requests/month; Standard Parameter Store and standard-throughput calls are free.',
        beyond: '$1/month per customer-managed KMS key and $0.03/10,000 eligible KMS requests.',
        source: pricingSources.systemsManager,
    },
    {
        service: 'Route 53',
        included: 'Alias A/AAAA queries to the CloudFront distribution are free.',
        beyond: '$0.50/month for the hosted zone; non-alias standard queries start at $0.40/M. Domain registration is separate.',
        source: pricingSources.route53,
    },
];

function PricingLink({ href, children }) {
    return (
        <a href={href} target="_blank" rel="noreferrer">
            {children}<span className="cost-external-mark" aria-hidden="true">↗</span>
        </a>
    );
}

export default function CostPage() {
    return (
        <main className="cost-page">
            <header className="cost-hero">
                <div>
                    <span className="cost-kicker">AWS cost estimate · us-east-1 · USD</span>
                    <h1>What it costs to run CloudDSP.</h1>
                    <p>
                        A transparent estimate for the deployed serverless and GPU architecture. The headline model uses
                        one isolated six-stem job per active user per day and deliberately separates per-job usage from
                        infrastructure shared by every user.
                    </p>
                </div>
                <span className="cost-reviewed">Pricing reviewed 27 Aug 2026</span>
            </header>

            <section className="cost-summary" aria-label="Cost estimate summary">
                <div>
                    <span>Standard direct job</span>
                    <strong>$0.231</strong>
                    <small>while shared Lambda, transfer, and log allowances remain</small>
                </div>
                <div>
                    <span>After Lambda free pool</span>
                    <strong>~$0.245</strong>
                    <small>before direct S3 internet transfer</small>
                </div>
                <div>
                    <span>One job each day</span>
                    <strong>$6.93</strong>
                    <small>30-day workload estimate</small>
                </div>
                <div>
                    <span>GPU share</span>
                    <strong>~95%</strong>
                    <small>of the within-allowance per-job total</small>
                </div>
            </section>

            <aside className="cost-notice">
                <strong>Estimate, not a quote.</strong> Prices are public us-east-1 rates and exclude tax, domain
                registration, AWS Support, the residential proxy provider, and data transferred through that proxy.
                Free tiers are account-wide—not granted independently to each user—and AWS can change pricing.
            </aside>

            <section className="cost-section">
                <div className="cost-section-heading">
                    <div>
                        <span className="cost-section-number">01</span>
                        <div>
                            <span className="cost-kicker">Model assumptions</span>
                            <h2>The representative daily user</h2>
                        </div>
                    </div>
                    <p>The values below make the estimate reproducible and easy to replace with measured production averages.</p>
                </div>
                <dl className="cost-assumptions">
                    <div><dt>Workload</dt><dd>One authenticated direct-upload job per user per day, using six-stem separation.</dd></div>
                    <div><dt>GPU window</dt><dd>25 billed g4dn.xlarge minutes: launch/image preparation, processing, and the configured 20-minute post-job reuse window in aggregate.</dd></div>
                    <div><dt>MIDI fan-out</dt><dd>Five Basic Pitch functions for pitched stems and one ADTOF function for drums, each configured at 3,008 MiB.</dd></div>
                    <div><dt>Artifacts</dt><dd>Approximately 0.75 GB across the source, six WAV stems, MIDI, and BPM data, retained for 14 days.</dd></div>
                    <div><dt>Client activity</dt><dd>Snapshot polling every five seconds while pending, one 25-minute WebSocket session, and one complete artifact download.</dd></div>
                    <div><dt>Capacity</dt><dd>Batch is capped at 8 vCPUs, or two g4dn.xlarge hosts. This limits concurrency, not monthly spend.</dd></div>
                </dl>
            </section>

            <section className="cost-section">
                <div className="cost-section-heading">
                    <div>
                        <span className="cost-section-number">02</span>
                        <div>
                            <span className="cost-kicker">Dominant cost</span>
                            <h2>GPU stem separation</h2>
                        </div>
                    </div>
                    <PricingLink href={pricingSources.ec2}>EC2 pricing</PricingLink>
                </div>
                <div className="cost-gpu-equation" aria-label="GPU cost calculation">
                    <div><span>g4dn.xlarge</span><strong>$0.526</strong><small>per hour</small></div>
                    <b aria-hidden="true">×</b>
                    <div><span>Billed window</span><strong>25 / 60</strong><small>of an hour</small></div>
                    <b aria-hidden="true">=</b>
                    <div className="is-total"><span>GPU instance</span><strong>$0.2192</strong><small>per isolated job</small></div>
                </div>
                <div className="cost-explanation-grid">
                    <p>
                        The 20-minute setting is an <strong>instance scale-down delay after the last job</strong>, not
                        extra processing time inside the job. A nearby job can reuse that warm host and share the idle
                        window, reducing marginal cost. Sparse jobs pay the most because each can create a separate host window.
                    </p>
                    <p>
                        AWS does not bill Batch as a separate scheduler. EC2 billing begins when the instance runs; image
                        pulls and ECS startup occurring on that running host are billable. Public IPv4 and the temporary
                        EBS root volume add about $0.0035 to this 25-minute model.
                    </p>
                </div>
                <div className="cost-capacity-note">
                    <strong>Fleet ceiling:</strong> two continuously busy g4dn.xlarge hosts cost $1.052/hour for EC2,
                    or about $757.44 for a 720-hour month before IPv4, EBS, Lambda, storage, and transfer. The 8-vCPU
                    setting is therefore a rate limiter, not a hard monthly budget.
                </div>
            </section>

            <section className="cost-section">
                <div className="cost-section-heading">
                    <div>
                        <span className="cost-section-number">03</span>
                        <div>
                            <span className="cost-kicker">Per-job ledger</span>
                            <h2>Every AWS service touched by one job</h2>
                        </div>
                    </div>
                    <p>An asterisk marks a marginal price after that service's shared account allowance is exhausted.</p>
                </div>
                <div className="cost-table-scroll">
                    <table className="cost-table">
                        <thead>
                            <tr><th>Service</th><th>Modeled use</th><th>Public rate</th><th>Free tier / allowance</th><th>Estimate per job</th></tr>
                        </thead>
                        <tbody>
                            {perJobCosts.map((item) => (
                                <tr key={item.service}>
                                    <th scope="row"><PricingLink href={item.source}>{item.service}</PricingLink></th>
                                    <td>{item.usage}</td>
                                    <td>{item.rate}</td>
                                    <td>{item.freeTier}</td>
                                    <td className="cost-number">{item.estimate}</td>
                                </tr>
                            ))}
                        </tbody>
                        <tfoot>
                            <tr><th scope="row" colSpan="4">Representative direct-upload job while shared allowances remain</th><td className="cost-number">$0.231</td></tr>
                        </tfoot>
                    </table>
                </div>
                <p className="cost-footnote">
                    * From 1–27 Aug 2026, 14 six-stem jobs averaged 45.86 seconds for each Basic Pitch invocation and
                    48.77 seconds for ADTOF: 816.86 GB-seconds, or about $0.01362/job after the Lambda compute allowance.
                    The 400,000 GB-second pool covers roughly 489 jobs at that observed mix. Actual cost scales with stem
                    length, retries, configured memory, and model runtime. yt-dlp
                    adds roughly $0.0059 for a modeled two-minute 3,008 MiB invocation after the Lambda free pool, plus the
                    external proxy provider's fee.
                </p>
            </section>

            <section className="cost-section">
                <div className="cost-section-heading">
                    <div>
                        <span className="cost-section-number">04</span>
                        <div>
                            <span className="cost-kicker">Shared baseline</span>
                            <h2>Costs that do not belong to one user</h2>
                        </div>
                    </div>
                    <strong className="cost-section-total">~$1.07 / month now</strong>
                </div>
                <div className="cost-table-scroll">
                    <table className="cost-table cost-table--shared">
                        <thead><tr><th>Service</th><th>Current basis</th><th>Estimate</th><th>What changes it</th></tr></thead>
                        <tbody>
                            {monthlySharedCosts.map((item) => (
                                <tr key={item.service}>
                                    <th scope="row"><PricingLink href={item.source}>{item.service}</PricingLink></th>
                                    <td>{item.basis}</td>
                                    <td className="cost-number">{item.estimate}</td>
                                    <td>{item.note}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
                <p className="cost-footnote">
                    The current known baseline is approximately $0.55 ECR + $0.50 Route 53 + $0.02 static/demo S3.
                    If the optional yt-dlp proxy SecureString creates its customer-managed KMS key, the known baseline
                    becomes approximately $2.07/month. Divide shared cost by active users only for internal allocation;
                    AWS bills the account, not each user.
                </p>
            </section>

            <section className="cost-section">
                <div className="cost-section-heading">
                    <div>
                        <span className="cost-section-number">05</span>
                        <div>
                            <span className="cost-kicker">Scale scenarios</span>
                            <h2>Estimated cost per active user</h2>
                        </div>
                    </div>
                    <p>Thirty-day month; costs assume each job creates its own 25-minute GPU window unless stated otherwise.</p>
                </div>
                <div className="cost-scenarios">
                    <div><span>One direct job each day</span><strong>$6.93</strong><small>within shared Lambda, API, transfer, and log allowances</small></div>
                    <div><span>After Lambda free pool</span><strong>~$7.35</strong><small>adds about $0.41 of measured MIDI Lambda compute</small></div>
                    <div><span>Lambda + billed S3 transfer</span><strong>~$8.97–$9.78</strong><small>also adds $1.62–$2.43 for 18–27 GB after the account's first 100 GB</small></div>
                    <div><span>Single-user account, current shared base</span><strong>~$8.00</strong><small>$6.93 workload + $1.07 current shared infrastructure</small></div>
                </div>
                <div className="cost-scenario-details">
                    <p><strong>Linked media:</strong> after the Lambda pool is consumed, add about $0.18/user-month for one modeled two-minute yt-dlp run each day. Residential proxy traffic and subscription charges remain external.</p>
                    <p><strong>Daily quota ceiling:</strong> five direct and three URL jobs every day can approach roughly $59/user-month before S3 transfer and shared infrastructure if every submission receives a separate 25-minute GPU window.</p>
                    <p><strong>Warm-host reuse:</strong> several jobs arriving within the 20-minute delay can use the same instance. In that case, allocate the host's measured running minutes across those jobs instead of multiplying $0.2192 by every job.</p>
                </div>
                <div className="cost-capacity-note">
                    <strong>Fully metered midpoint:</strong> with Lambda and direct-S3 transfer allowances exhausted, the
                    representative workload is about $9.37/user-month. Adding the current $1.07 shared baseline produces
                    an approximately $10.44/month single-user deployment.
                </div>
            </section>

            <section className="cost-section">
                <div className="cost-section-heading">
                    <div>
                        <span className="cost-section-number">06</span>
                        <div>
                            <span className="cost-kicker">Beyond free usage</span>
                            <h2>Allowance and overage reference</h2>
                        </div>
                    </div>
                    <p>Use this table to revise the model when account-wide traffic crosses a threshold.</p>
                </div>
                <div className="cost-table-scroll">
                    <table className="cost-table cost-table--tiers">
                        <thead><tr><th>Service</th><th>Included usage</th><th>Beyond included usage</th></tr></thead>
                        <tbody>
                            {scaleRates.map((item) => (
                                <tr key={item.service}>
                                    <th scope="row"><PricingLink href={item.source}>{item.service}</PricingLink></th>
                                    <td>{item.included}</td>
                                    <td>{item.beyond}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            </section>

            <section className="cost-section cost-section--final">
                <div className="cost-section-heading">
                    <div>
                        <span className="cost-section-number">07</span>
                        <div>
                            <span className="cost-kicker">Services with no modeled charge</span>
                            <h2>What is deliberately $0</h2>
                        </div>
                    </div>
                </div>
                <p>
                    IAM, CloudFormation, the Internet Gateway, the S3 gateway VPC endpoint, ACM certificates attached to
                    CloudFront, AWS-owned encryption keys, and AWS Batch orchestration have no separate hourly fee in this
                    architecture. There is no NAT Gateway and no interface VPC endpoint fleet. The application also does
                    not provision WAF, provisioned Lambda concurrency, DynamoDB provisioned capacity, or an always-running
                    EC2 host. Those are valid future controls, but adding them would change this cost model.
                </p>
            </section>
        </main>
    );
}
