
#include <memory>

#include <arrow/io/interfaces.h>
#include <aws/core/auth/AWSCredentials.h>
#include <aws/core/auth/AWSCredentialsProvider.h>
#include <curl/curl.h>

void s3_open_file(const char *fname,
                  std::shared_ptr<::arrow::io::RandomAccessFile> *file,
                  const char *bucket_region, bool anonymous);

/**
 * Parse DEFAULT_ICEBERG_REST_AWS_CREDENTIALS_PROVIDER_TIMEOUT for the timeout
 * in minutes, otherwise 15.
 * Primarily for testing purposes.
 */
unsigned int get_default_credential_timeout();

/**
 * Returns true if DEBUG_ICEBERG_REST_AWS_CREDENTIALS_PROVIDER == "1"
 * Primarily for testing purposes.
 */
bool get_debug_credentials_provider();

// AWS credentials provider that uses the Iceberg REST API to get temporary
// credentials.
struct IcebergRestAwsCredentialsProvider : Aws::Auth::AWSCredentialsProvider {
    /**
     * Construct a new IcebergRestAwsCredentialsProvider object
     *
     * @param _catalog_uri URI of the Iceberg catalog
     * @param _bearer_token Bearer token to authenticate with the Iceberg
     * catalog
     * @param _warehouse Warehouse name
     * @param _schema Schema name
     * @param _table Table name
     */
    IcebergRestAwsCredentialsProvider(
        const std::string_view _catalog_uri,
        const std::string_view _bearer_token, const std::string_view _warehouse,
        const std::string_view _schema, const std::string_view _table,
        const unsigned int _credential_timeout =
            get_default_credential_timeout(),
        const bool _debug = get_debug_credentials_provider())
        : catalog_uri(_catalog_uri),
          bearer_token(_bearer_token),
          warehouse(_warehouse),
          schema(_schema),
          table(_table),
          credential_timeout(_credential_timeout),
          debug(_debug) {
        hnd = curl_easy_init();
        // Generated by curl --libcurl
        curl_easy_setopt(hnd, CURLOPT_BUFFERSIZE, 102400L);
        curl_easy_setopt(hnd, CURLOPT_NOPROGRESS, 1L);
        curl_easy_setopt(hnd, CURLOPT_USERAGENT, "curl/7.88.1");
        curl_easy_setopt(hnd, CURLOPT_MAXREDIRS, 50L);
        curl_easy_setopt(hnd, CURLOPT_HTTP_VERSION,
                         (long)CURL_HTTP_VERSION_2TLS);
        curl_easy_setopt(hnd, CURLOPT_FTP_SKIP_PASV_IP, 1L);
        curl_easy_setopt(hnd, CURLOPT_TCP_KEEPALIVE, 1L);

        // Set a callback function to store the response from the Iceberg REST
        // API
        curl_easy_setopt(hnd, CURLOPT_WRITEFUNCTION, this->CurlWriteCallback);
        curl_easy_setopt(hnd, CURLOPT_WRITEDATA, &curl_buffer);
    }

    ~IcebergRestAwsCredentialsProvider() override { curl_easy_cleanup(hnd); }

    /**
     * Get the AWS credentials
     * This method will call the Iceberg REST API to get temporary AWS
     * credentials if the current credentials are expired.
     *
     * @return Aws::Auth::AWSCredentials
     */
    Aws::Auth::AWSCredentials GetAWSCredentials() override;
    /**
     * Reload the AWS credentials
     * This method will call the Iceberg REST API to get temporary AWS
     * credentials.
     */
    void Reload() override;

    /**
     * Get an OAuth2 token from the Iceberg REST Catalog at base_url
     * @param base_url Url of the Iceberg REST Catalog to fetch the token from
     * @param credential Credential to exchange for a token, crednials should be
     * of the form "client_id:client_secret" and can be generated in the Tabular
     * UI for Tabular REST Catalogs
     * @returns the token
     */
    static std::string getToken(const std::string_view base_url,
                                const std::string_view credential);

    /**
     * @brief Get the stored region, otherwise reload and return fetched region
     * @returns AWS region from Iceberg Catalog
     */
    std::string GetRegion() {
        if (this->region.empty()) {
            this->Reload();
        }
        return std::string(this->region);
    }

   protected:
    // URI of the Iceberg catalog
    const std::string catalog_uri;
    // Bearer token to authenticate with the Iceberg catalog
    const std::string bearer_token;
    // Warehouse name
    const std::string warehouse;
    // Schema name
    const std::string schema;
    // Table name
    const std::string table;
    // Credential timeout in minutes
    const unsigned int credential_timeout;
    // Whether to print debug messages
    const bool debug;
    // AWS region of the Warehouse
    std::string region;

    // Cached AWS credentials
    Aws::Auth::AWSCredentials credentials;

    // CURL handle
    CURL *hnd;
    // Buffer to store the response from the Iceberg REST API
    std::string curl_buffer;
    static const unsigned int n_retries = 3;

    /**
     * Callback function for CURL
     * Simply appends the response to the buffer
     */
    static size_t CurlWriteCallback(void *contents, size_t size, size_t nmemb,
                                    std::string *s) {
        size_t new_len = s->size() + size * nmemb;
        s->resize(new_len);
        std::copy((char *)contents, (char *)contents + size * nmemb,
                  s->begin() + new_len - size * nmemb);
        return size * nmemb;
    }

    /**
     * Get the warehouse prefix and token from the Icberg REST API
     * @returns prefix, token, region
     */
    std::pair<const std::string, const std::string> get_warehouse_config();

    /**
     * Get the AWS credentlal and region values for table from the Iceberg REST
     * API
     * @returns access_key, secret_key, session_token, region
     */
    std::tuple<const std::string, const std::string, const std::string,
               const std::string>
    get_aws_credentials_from_rest_catalog(
        const std::string_view prefix, const std::string_view warehouse_token);
};
