// Copyright (C) 2023 Bodo Inc. All rights reserved.
#pragma once

#include <arrow/result.h>

// TODO Tell the compiler that the branch is unlikely.
#define CHECK_ARROW_MEM(expr, msg)                                      \
    if (!(expr.ok())) {                                                 \
        std::string err_msg = std::string(msg) + " " + expr.ToString(); \
        throw std::runtime_error(err_msg);                              \
    }

#define CHECK_ARROW_MEM_RET(expr, msg)                                      \
    {                                                                       \
        auto stat = expr;                                                   \
        if (!stat.ok()) {                                                   \
            std::string err_msg = std::string(msg) + " " + stat.ToString(); \
            return stat.WithMessage(err_msg);                               \
        }                                                                   \
    }

namespace bodo {

// --------------------------- Storage Options --------------------------- //
/// @brief Enum to Indicate which Manager to Use
enum StorageType : uint8_t {
    Local = 0,
    S3 = 1,
};

/// @brief Options for Storage Manager Implementations
struct StorageOptions {
    /// @brief Amount of bytes allowed to be spilled to
    /// storage location
    int64_t usable_size = 1024ll * 1024ll * 1024ll;

    /// @brief Location / folder to write block spill files
    std::string location;

    /// @brief Type of StorageManager to use
    StorageType type = StorageType::Local;

    static std::shared_ptr<StorageOptions> Defaults(uint8_t tier);
};

// --------------------------- Storage Manager --------------------------- //
/// @brief Abstract Class / Interface for Storage Managers
/// Storage Managers manage the reading + writing of blocks
/// from a storage location as well as size limitations
class StorageManager {
   public:
    StorageManager(std::string storage_name,
                   const std::shared_ptr<StorageOptions> options);

    // Required otherwise won't compile
    // All cleanup operations occur within the Cleanup virtual function
    // so that exceptions can be safely raised
    // Cleanup runs at program exit. However, if we never want cleanup
    // to raise errors, we should move it back to the destructor
    // TODO: Revisit in the future
    virtual ~StorageManager() = default;

    /// @brief How many bytes are available to be stored in this
    /// storage location. -1 indicates unlimited
    inline int64_t usable_size() const { return options->usable_size; }

    /**
     * @brief Is there space available in this storage location for
     * allocations to be spilled to it
     *
     * @param amount Bytes to potentially be spilled
     * @return true If allocation can be spilled to it
     * @return false If allocation can not be spilled here
     */
    bool CanSpillTo(uint64_t amount) const {
        return options->usable_size < 0 ||
               (curr_spilled_bytes + amount) <=
                   static_cast<uint64_t>(options->usable_size);
    }

    /**
     * @brief Update the current number of spilled bytes to this
     * storage location by diff
     *
     * @param diff The number of bytes added or removed from this
     * storage location.
     */
    void UpdateSpilledBytes(int64_t diff) { this->curr_spilled_bytes += diff; }

    /// @brief Get the next available block id for this storage location
    uint64_t GetNewBlockID() { return this->block_id_counter++; }

    // ------------------------- Virtual Functions ------------------------- //

    /// @brief Initialize the Manager by Setting Up any
    /// Background Resources
    virtual arrow::Status Initialize() = 0;

    /**
     * @brief Read a block with id block_id and size of n_bytes
     * from storage, write its contents to out_ptr, and delete
     * block from storage.
     *
     * @param[in] block_id Index of block to read from storage
     * @param[in] n_bytes Size of block in bytes
     * @param[out] out_ptr Location to write contents to
     * @return arrow::Status Ok if success, otherwise a potential
     * filesystem error raised during read.
     */
    virtual arrow::Status ReadBlock(uint64_t block_id, int64_t n_bytes,
                                    uint8_t* out_ptr) = 0;

    /**
     * @brief Write the contents of a frame located at in_ptr
     * and size frame_size to storage, and return the new block id.
     *
     * @param in_ptr Location of frame to write
     * @param frame_size Size of frame in bytes
     * @return arrow::Result<uint64_t> Block ID of contents if success.
     * Otherwise a potential filesystem error raised during write
     */
    virtual arrow::Result<uint64_t> WriteBlock(uint8_t* in_ptr,
                                               uint64_t frame_size) = 0;

    /**
     * @brief Delete a block with id block_id and size of n_bytes from
     * storage.
     *
     * @param block_id Index of block to delete from storage
     * @param n_bytes Size of block in bytes
     * @return arrow::Status Ok if success, otherwise a potential
     * filesystem error raised during delete.
     */
    virtual arrow::Status DeleteBlock(uint64_t block_id, int64_t n_bytes) = 0;

    /// @brief Cleanup any leftover spill files
    /// Expected to run during program exit and can throw
    /// an error on fail
    virtual void Cleanup() = 0;

   protected:
    /// @brief Name of the manager
    /// What to refer to in logs
    std::string storage_name;

    /// @brief Rank and process-unique identifier for
    /// spilling. Can be used for location handling
    std::string uuid;

    /// @brief Configuration Options
    std::shared_ptr<StorageOptions> options;

   private:
    /// @brief Increment every time we write a block to disk
    uint64_t block_id_counter = 0;

    /// @brief Current # of bytes spilled to storage
    uint64_t curr_spilled_bytes = 0;
};

/// @brief Factory Function to Create StorageManager based on StorageOptions
std::unique_ptr<StorageManager> MakeStorageManager(
    const std::shared_ptr<StorageOptions>& options);

};  // namespace bodo