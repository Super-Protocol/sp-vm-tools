import { ChallengeProviderSgx, PkiClient } from "@super-protocol/pki-client";
import { StaticAttestationServiceClient } from "@super-protocol/pki-api-client";
import * as fs from 'fs';
import * as path from 'path';

const requestSecretFromVault = async (
    caUrl: string,
    caBundlePath: string,
    certDomain: string,
    outputCertFolder: string
) => {
    try {
        const challengeProvider = new ChallengeProviderSgx();
        const caBundle = fs.readFileSync(caBundlePath, 'utf-8');

        const attestationServiceClient = new StaticAttestationServiceClient(
            caUrl,
            caBundle
        );

        const pkiClient = new PkiClient({
            challengeProvider,
            attestationServiceClient
        });

        const certDomainGenerated = await pkiClient.generateSslCertificate([certDomain]);

        fs.writeFileSync(path.join(outputCertFolder, certDomain + '.crt'), certDomainGenerated.caBundle, 'utf-8');
        fs.writeFileSync(path.join(outputCertFolder, certDomain + '.key'), certDomainGenerated.keyPair.privateKeyPem, 'utf-8');

        console.log(`Certificate ${certDomainGenerated} stored successfull to ${outputCertFolder}`);
    } catch (error) {
        console.error(`Certificate generation error ${error}`);
    }
};

const args = process.argv.slice(2);
if (args.length < 4) {
    console.error('Usage: ./ca-initializer <CA_URL> <CA_BUNDLE_PATH> <CERT_GENERATED_DOMAIN> <OUTPUT_CERTS_FOLDER>');
    process.exit(1);
}

const [caUrl, caBundlePath, certDomain, outputCertFolder] = args;

requestSecretFromVault(caUrl, caBundlePath, certDomain, outputCertFolder);
