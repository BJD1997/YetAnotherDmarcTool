export interface LocalLoginResponse {
  needs_enrollment: boolean;
}

export interface EnrollOtpResponse {
  secret: string;
  qr_code_data_uri: string;
}

export interface EnrollOtpConfirmResponse {
  recovery_codes: string[];
}
