import * as fs from 'fs';
import * as path from 'path';
import { ChallengeProvider, ChallengeProviderSevSnp, ChallengeProviderTdx, ChallengeProviderUntrusted, PkiClient } from "@super-protocol/pki-client";
import { StaticAttestationServiceClient } from "@super-protocol/pki-api-client";
import { ChallengeType } from "@super-protocol/pki-common";

const requestSecretFromVault = async (
    cpuType: string,
    caBundlePath: string,
    certDomain: string,
    outputCertFolder: string
) => {
    try {
        const challengeProvider = getChallengeProvider(cpuType);
        const caUrl = getCaUrl(cpuType);
        const caBundle = fs.readFileSync(caBundlePath, 'utf-8');

        const attestationServiceClient = new StaticAttestationServiceClient(
            caUrl + '/api/v1/pki',
            caBundle
        );

        const pkiClient = new PkiClient({
            challengeProvider,
            attestationServiceClient
        });

        const certDomainGenerated = await pkiClient.generateSslCertificate([certDomain]);

        fs.writeFileSync(path.join(outputCertFolder, certDomain + '.crt'), certDomainGenerated.certPem, 'utf-8');
        fs.writeFileSync(path.join(outputCertFolder, certDomain + '.ca.crt'), certDomainGenerated.caBundle, 'utf-8');
        fs.writeFileSync(path.join(outputCertFolder, certDomain + '.key'), certDomainGenerated.keyPair.privateKeyPem, 'utf-8');

        console.log(`Certificate ${certDomainGenerated} stored successfull to ${outputCertFolder}`);
    } catch (error) {
        console.error(`Certificate generation error ${error}`);
    }
};

const getCaUrl = (cpuType: string): string => {
  if(cpuType === 'Untrusted') {
    return 'https://ca-subroot1.tee-dev.superprotocol.com:44443';
  }

  return 'https://ca-subroot2.tee-dev.superprotocol.io:44443'
};

const getChallengeProvider = (cpuType: string): ChallengeProvider => {
  // cpuTypes have same name as ChallengeType enum
  // in @super-protocol/pki-common
    switch (cpuType) {
      case ChallengeType.Untrusted:
        return new ChallengeProviderUntrusted(Buffer.from('cccccc', 'hex'));
      case ChallengeType.TDX:
        return new ChallengeProviderTdx();
      case ChallengeType.SEVSNP:
        return new ChallengeProviderSevSnp();
      default:
        throw new Error(`Unsupported CPU type: ${cpuType}`);
    }
};

const args = process.argv.slice(2);
if (args.length < 4) {
    console.error('Usage: ./ca-initializer <CPU_TYPE> <CA_URL> <CA_BUNDLE_PATH> <CERT_GENERATED_DOMAIN> <OUTPUT_CERTS_FOLDER>');
    process.exit(1);
}

const [cpuType, caUrl, caBundlePath, certDomain, outputCertFolder] = args;

requestSecretFromVault(cpuType, caBundlePath, certDomain, outputCertFolder);
